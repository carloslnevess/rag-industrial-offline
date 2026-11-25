using System.Security.Cryptography;
using System.Text;
using IndustrialRag.Api.Models;
using Npgsql;

namespace IndustrialRag.Api.Services;

public class AuthService
{
    private readonly string _dbDsn;

    public AuthService(IConfiguration config)
    {
        _dbDsn = Environment.GetEnvironmentVariable("DB_CONNECTION")
            ?? config.GetConnectionString("Postgres")
            ?? "Host=localhost;Port=5432;Database=industrial_rag;Username=rag_admin;Password=rag_password_dev";
    }

    public async Task GarantirSchemaAuthAsync(CancellationToken ct = default)
    {
        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);

        const string sql = """
            CREATE TABLE IF NOT EXISTS usuarios (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                username VARCHAR(60) UNIQUE NOT NULL,
                nome_exibicao VARCHAR(120) NOT NULL,
                senha_hash TEXT NOT NULL,
                senha_salt TEXT NOT NULL,
                perfil VARCHAR(20) NOT NULL DEFAULT 'operador',
                ativo BOOLEAN NOT NULL DEFAULT TRUE,
                criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS usuarios_tokens (
                token TEXT PRIMARY KEY,
                usuario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                expira_em TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '30 days')
            );
            CREATE TABLE IF NOT EXISTS sessoes_chat (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                usuario_id UUID NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
                titulo VARCHAR(160) NOT NULL,
                criado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            ALTER TABLE historico_analises
                ADD COLUMN IF NOT EXISTS sessao_id UUID REFERENCES sessoes_chat(id) ON DELETE SET NULL,
                ADD COLUMN IF NOT EXISTS usuario_id UUID REFERENCES usuarios(id) ON DELETE SET NULL;
            """;

        await using (var cmd = new NpgsqlCommand(sql, conn))
            await cmd.ExecuteNonQueryAsync(ct);

        // Cria usuario padrao admin/admin123 se a tabela estiver vazia
        await using var chk = new NpgsqlCommand("SELECT COUNT(*) FROM usuarios", conn);
        var total = Convert.ToInt64(await chk.ExecuteScalarAsync(ct));
        if (total == 0)
        {
            var (hash, salt) = GerarHashSenha("admin123");
            await using var ins = new NpgsqlCommand("""
                INSERT INTO usuarios (username, nome_exibicao, senha_hash, senha_salt, perfil, ativo)
                VALUES ('admin', 'Administrador', @h, @s, 'admin', TRUE);
                """, conn);
            ins.Parameters.AddWithValue("h", hash);
            ins.Parameters.AddWithValue("s", salt);
            await ins.ExecuteNonQueryAsync(ct);
            Console.WriteLine("[AUTH] Usuario inicial criado: admin / admin123 (altere no Gerenciador porta 8090)");
        }
    }

    public static (string HashBase64, string SaltBase64) GerarHashSenha(string senha)
    {
        var salt = RandomNumberGenerator.GetBytes(16);
        var hash = Rfc2898DeriveBytes.Pbkdf2(Encoding.UTF8.GetBytes(senha), salt, 100_000, HashAlgorithmName.SHA256, 32);
        return (Convert.ToBase64String(hash), Convert.ToBase64String(salt));
    }

    private static bool VerificarSenha(string senha, string hashBase64, string saltBase64)
    {
        var salt = Convert.FromBase64String(saltBase64);
        var hashEsperado = Convert.FromBase64String(hashBase64);
        var hashAtual = Rfc2898DeriveBytes.Pbkdf2(Encoding.UTF8.GetBytes(senha), salt, 100_000, HashAlgorithmName.SHA256, 32);
        return CryptographicOperations.FixedTimeEquals(hashAtual, hashEsperado);
    }

    public async Task<(bool Ok, string? Erro, AuthRespostaDto? Dados)> LoginAsync(LoginRequest req, CancellationToken ct)
    {
        var user = (req.Username ?? "").Trim().ToLowerInvariant();
        var senha = req.Senha ?? "";

        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);

        Guid userId;
        string nome, hash, salt, perfil;
        bool ativo;

        await using (var cmd = new NpgsqlCommand("""
            SELECT id, nome_exibicao, senha_hash, senha_salt, perfil, ativo
            FROM usuarios WHERE username = @u;
            """, conn))
        {
            cmd.Parameters.AddWithValue("u", user);
            await using var r = await cmd.ExecuteReaderAsync(ct);
            if (!await r.ReadAsync(ct)) return (false, "Usu\u00e1rio ou senha inv\u00e1lidos.", null);
            userId = r.GetGuid(0);
            nome = r.GetString(1);
            hash = r.GetString(2);
            salt = r.GetString(3);
            perfil = r.GetString(4);
            ativo = r.GetBoolean(5);
        }

        if (!ativo) return (false, "Conta desativada pelo administrador.", null);
        if (!VerificarSenha(senha, hash, salt)) return (false, "Usu\u00e1rio ou senha inv\u00e1lidos.", null);

        var token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
        await using (var tok = new NpgsqlCommand("INSERT INTO usuarios_tokens (token, usuario_id) VALUES (@t, @uid)", conn))
        {
            tok.Parameters.AddWithValue("t", token);
            tok.Parameters.AddWithValue("uid", userId);
            await tok.ExecuteNonQueryAsync(ct);
        }

        return (true, null, new AuthRespostaDto(token, new UsuarioAutenticadoDto(userId.ToString(), user, nome, perfil)));
    }

    public async Task<UsuarioAutenticadoDto?> ValidarTokenAsync(string? token, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(token)) return null;
        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);

        const string sql = """
            SELECT u.id::text, u.username, u.nome_exibicao, u.perfil
            FROM usuarios_tokens t
            JOIN usuarios u ON u.id = t.usuario_id
            WHERE t.token = @t AND t.expira_em > NOW() AND u.ativo = TRUE;
            """;

        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("t", token.Trim());
        await using var r = await cmd.ExecuteReaderAsync(ct);
        if (!await r.ReadAsync(ct)) return null;
        return new UsuarioAutenticadoDto(r.GetString(0), r.GetString(1), r.GetString(2), r.GetString(3));
    }

    public async Task LogoutAsync(string? token, CancellationToken ct = default)
    {
        if (string.IsNullOrWhiteSpace(token)) return;
        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);
        await using var cmd = new NpgsqlCommand("DELETE FROM usuarios_tokens WHERE token = @t", conn);
        cmd.Parameters.AddWithValue("t", token.Trim());
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public static string? ExtrairToken(HttpContext ctx)
    {
        var auth = ctx.Request.Headers.Authorization.FirstOrDefault();
        if (!string.IsNullOrWhiteSpace(auth) && auth.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
            return auth["Bearer ".Length..].Trim();
        return ctx.Request.Headers["X-Auth-Token"].FirstOrDefault()?.Trim();
    }
}