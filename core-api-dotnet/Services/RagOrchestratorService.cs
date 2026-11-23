using System.Globalization;
using System.Net.Http.Json;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using IndustrialRag.Api.Models;
using Npgsql;
using NpgsqlTypes;

namespace IndustrialRag.Api.Services;

public class RagOrchestratorService
{
    private readonly HttpClient _http;
    private readonly string _dbDsn;
    private readonly string _pythonWorkerUrl;
    private readonly string _ollamaUrl;
    private readonly string _llmModel;

    private static readonly string[] FrasesCasuais =
    [
        "oi", "ola", "opa", "eae", "e ai", "salve", "hello", "hi",
        "bom dia", "boa tarde", "boa noite",
        "tudo bem", "tudo certo", "como vai", "beleza", "blz", "joia",
        "obrigado", "obrigada", "valeu", "vlw", "show", "perfeito", "entendi", "ok", "okay", "legal", "top",
        "tchau", "ate mais", "ate logo",
        "quem e voce", "o que voce faz", "como voce funciona", "me ajuda", "ajuda"
    ];

    private static readonly HashSet<string> StopwordsPt = new(StringComparer.OrdinalIgnoreCase)
    {
        "para", "com", "como", "qual", "quais", "que", "uma", "uns", "umas",
        "dos", "das", "nos", "nas", "por", "mais", "sobre", "esse", "essa",
        "isso", "este", "esta", "voce", "pode", "fazer", "preciso", "quero",
        "favor", "temos", "tenho", "estao", "esta", "aqui", "onde", "quando",
        "porque", "pelo", "pela", "entre", "muito", "muita", "tambem", "agora"
    };

    public string ModeloAtual => _llmModel;

    public RagOrchestratorService(HttpClient http, IConfiguration config)
    {
        _http = http;
        _http.Timeout = TimeSpan.FromMinutes(5);

        _dbDsn = Environment.GetEnvironmentVariable("DB_CONNECTION")
            ?? config.GetConnectionString("Postgres")
            ?? "Host=localhost;Port=5432;Database=industrial_rag;Username=rag_admin;Password=rag_password_dev";

        _pythonWorkerUrl = Environment.GetEnvironmentVariable("PYTHON_WORKER_URL")
            ?? "http://localhost:8001";

        _ollamaUrl = Environment.GetEnvironmentVariable("OLLAMA_URL")
            ?? "http://localhost:11434";

        _llmModel = Environment.GetEnvironmentVariable("LLM_MODEL")
            ?? "qwen3:4b";
    }

    public async Task GarantirSchemaAprendizadoAsync(CancellationToken ct = default)
    {
        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);

        const string sql = """
            ALTER TABLE historico_analises
                ADD COLUMN IF NOT EXISTS avaliacao VARCHAR(20),
                ADD COLUMN IF NOT EXISTS correcao_operador TEXT,
                ADD COLUMN IF NOT EXISTS aprovado_conhecimento BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS avaliado_em TIMESTAMPTZ;

            CREATE INDEX IF NOT EXISTS idx_historico_aprovado_fts
                ON historico_analises
                USING GIN (
                    to_tsvector(
                        'portuguese',
                        coalesce(pergunta, '') || ' ' || coalesce(correcao_operador, '') || ' ' || coalesce(resposta, '')
                    )
                )
                WHERE aprovado_conhecimento = TRUE;
            """;

        await using var cmd = new NpgsqlCommand(sql, conn);
        await cmd.ExecuteNonQueryAsync(ct);
    }

    public bool EhConversaCasual(string pergunta)
    {
        if (string.IsNullOrWhiteSpace(pergunta)) return true;

        var limpa = RemoverAcentos(pergunta.Trim().ToLowerInvariant());
        limpa = Regex.Replace(limpa, @"[^\w\s]", " ");
        limpa = Regex.Replace(limpa, @"\s+", " ").Trim();

        if (limpa.Length > 45 || Regex.IsMatch(limpa, @"\d"))
            return false;

        foreach (var frase in FrasesCasuais)
        {
            if (limpa == frase || (limpa.StartsWith(frase + " ") && limpa.Length <= frase.Length + 12))
                return true;
        }

        return false;
    }

    public bool DeveUsarRaciocinio(ConsultaRequest req)
    {
        if (EhConversaCasual(req.Pergunta))
            return false;

        if (req.RaciocinioProfundo)
            return true;

        return string.Equals(req.ModoOperacao, "diagnostico_falha", StringComparison.OrdinalIgnoreCase)
            || string.Equals(req.ModoOperacao, "comparar_padrao_ouro", StringComparison.OrdinalIgnoreCase);
    }

    public static string LimparPensamento(string texto)
    {
        if (string.IsNullOrWhiteSpace(texto)) return string.Empty;

        var limpo = Regex.Replace(texto, @"<think>[\s\S]*?</think>", string.Empty, RegexOptions.IgnoreCase);

        var idxFechamento = limpo.LastIndexOf("</think>", StringComparison.OrdinalIgnoreCase);
        if (idxFechamento >= 0)
        {
            limpo = limpo[(idxFechamento + "</think>".Length)..];
        }

        limpo = Regex.Replace(limpo, @"<think>[\s\S]*$", string.Empty, RegexOptions.IgnoreCase);

        return limpo.Trim();
    }

    private static string RemoverAcentos(string texto)
    {
        var norm = texto.Normalize(NormalizationForm.FormD);
        var sb = new StringBuilder(norm.Length);
        foreach (var c in norm)
        {
            if (CharUnicodeInfo.GetUnicodeCategory(c) != UnicodeCategory.NonSpacingMark)
                sb.Append(c);
        }
        return sb.ToString().Normalize(NormalizationForm.FormC);
    }

    private static string MontarQueryBusca(ConsultaRequest req)
    {
        var pergunta = req.Pergunta.Trim();

        if (pergunta.Length < 60 && req.Historico is { Count: > 0 })
        {
            var ultimaDoUsuario = req.Historico
                .LastOrDefault(m => string.Equals(m.Role, "user", StringComparison.OrdinalIgnoreCase)
                                    && !string.IsNullOrWhiteSpace(m.Content)
                                    && m.Content.Trim().Length >= 8);

            if (ultimaDoUsuario != null)
            {
                return $"{ultimaDoUsuario.Content.Trim()} | {pergunta}";
            }
        }

        return pergunta;
    }

    private static string MontarTsQuerySeguro(string texto)
    {
        var lower = texto.ToLowerInvariant();
        var brutos = Regex.Split(lower, @"[^\p{L}\p{N}]+")
            .Where(t => t.Length >= 3 && !StopwordsPt.Contains(RemoverAcentos(t)))
            .Distinct()
            .Take(8)
            .ToList();

        var termos = new List<string>();
        foreach (var t in brutos)
        {
            termos.Add(t);
            var semAcento = RemoverAcentos(t);
            if (!string.Equals(t, semAcento, StringComparison.OrdinalIgnoreCase))
                termos.Add(semAcento);
        }

        return string.Join(" | ", termos.Distinct());
    }

    public async Task<List<FragmentoDto>> BuscarContextoAsync(ConsultaRequest req, CancellationToken ct)
    {
        if (EhConversaCasual(req.Pergunta))
            return [];

        var queryBusca = MontarQueryBusca(req);

        if (string.Equals(req.ModoOperacao, "comparar_padrao_ouro", StringComparison.OrdinalIgnoreCase))
        {
            var tarefaBoas = ChamarWorkerSearchAsync(new BuscaPythonRequest
            {
                Query = queryBusca,
                Categoria = "04_historico_producao",
                Subcategoria = "padrao_ouro_boas",
                Maquina = req.Maquina,
                TopK = 3
            }, ct);

            var tarefaRuins = ChamarWorkerSearchAsync(new BuscaPythonRequest
            {
                Query = queryBusca,
                Categoria = "04_historico_producao",
                Subcategoria = "ocorrencias_ruins",
                Maquina = req.Maquina,
                TopK = 3
            }, ct);

            var tarefaParams = ChamarWorkerSearchAsync(new BuscaPythonRequest
            {
                Query = queryBusca,
                Categoria = "03_parametros_processo",
                Maquina = req.Maquina,
                TopK = 2
            }, ct);

            await Task.WhenAll(tarefaBoas, tarefaRuins, tarefaParams);

            var combinados = new List<FragmentoDto>();
            combinados.AddRange(tarefaBoas.Result);
            combinados.AddRange(tarefaRuins.Result);
            combinados.AddRange(tarefaParams.Result);

            if (combinados.Count > 0)
                return combinados;
        }

        return await ChamarWorkerSearchAsync(new BuscaPythonRequest
        {
            Query = queryBusca,
            Categoria = string.IsNullOrWhiteSpace(req.Categoria) ? null : req.Categoria,
            Maquina = string.IsNullOrWhiteSpace(req.Maquina) ? null : req.Maquina,
            TopK = req.TopK
        }, ct);
    }

    public async Task<List<CasoValidadoDto>> BuscarCasosValidadosAsync(ConsultaRequest req, CancellationToken ct)
    {
        if (EhConversaCasual(req.Pergunta))
            return [];

        var queryBusca = MontarQueryBusca(req);
        var tsQuery = MontarTsQuerySeguro(queryBusca);
        if (string.IsNullOrWhiteSpace(tsQuery))
            return [];

        var lista = new List<CasoValidadoDto>();

        try
        {
            await using var conn = new NpgsqlConnection(_dbDsn);
            await conn.OpenAsync(ct);

            var filtroMaquinaSql = string.IsNullOrWhiteSpace(req.Maquina)
                ? string.Empty
                : " AND (filtro_maquina IS NULL OR filtro_maquina = @maquina)";

            var sql = $"""
                SELECT id::text, pergunta, resposta, correcao_operador, filtro_maquina, filtro_categoria, criado_em,
                       ts_rank_cd(
                           setweight(to_tsvector('portuguese', coalesce(correcao_operador, '')), 'A') ||
                           setweight(to_tsvector('portuguese', coalesce(pergunta, '')), 'B') ||
                           setweight(to_tsvector('portuguese', coalesce(resposta, '')), 'C'),
                           to_tsquery('portuguese', @tsq)
                       ) AS score
                FROM historico_analises
                WHERE aprovado_conhecimento = TRUE
                  {filtroMaquinaSql}
                  AND (
                      to_tsvector(
                          'portuguese',
                          coalesce(pergunta, '') || ' ' || coalesce(correcao_operador, '') || ' ' || coalesce(resposta, '')
                      ) @@ to_tsquery('portuguese', @tsq)
                  )
                ORDER BY
                    CASE WHEN coalesce(correcao_operador, '') <> '' THEN 1 ELSE 0 END DESC,
                    score DESC,
                    criado_em DESC
                LIMIT 2;
                """;

            await using var cmd = new NpgsqlCommand(sql, conn);
            cmd.Parameters.AddWithValue("tsq", tsQuery);
            if (!string.IsNullOrWhiteSpace(req.Maquina))
                cmd.Parameters.AddWithValue("maquina", req.Maquina);

            await using var reader = await cmd.ExecuteReaderAsync(ct);
            while (await reader.ReadAsync(ct))
            {
                var score = reader.IsDBNull(7) ? 0.0 : Convert.ToDouble(reader.GetValue(7));
                if (score <= 0.0) continue;

                lista.Add(new CasoValidadoDto(
                    reader.GetString(0),
                    reader.GetString(1),
                    LimparPensamento(reader.GetString(2)),
                    reader.IsDBNull(3) ? null : reader.GetString(3),
                    reader.IsDBNull(4) ? null : reader.GetString(4),
                    reader.IsDBNull(5) ? null : reader.GetString(5),
                    reader.GetDateTime(6),
                    score
                ));
            }
        }
        catch (Exception ex)
        {
            Console.WriteLine($"[AVISO] Falha ao consultar casos validados: {ex.Message}");
        }

        return lista;
    }

    private async Task<List<FragmentoDto>> ChamarWorkerSearchAsync(BuscaPythonRequest payload, CancellationToken ct)
    {
        var response = await _http.PostAsJsonAsync($"{_pythonWorkerUrl}/search", payload, ct);
        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<List<FragmentoDto>>(cancellationToken: ct) ?? [];
    }

    public async IAsyncEnumerable<string> GerarRespostaStreamAsync(
        ConsultaRequest req,
        List<FragmentoDto> fragmentos,
        List<CasoValidadoDto> casosValidados,
        [EnumeratorCancellation] CancellationToken ct)
    {
        var casual = EhConversaCasual(req.Pergunta);
        var usarRaciocinio = DeveUsarRaciocinio(req);
        var systemPrompt = MontarPromptSistema(req.ModoOperacao);

        var messages = new List<object>
        {
            new { role = "system", content = systemPrompt }
        };

        if (req.Historico is { Count: > 0 })
        {
            foreach (var msg in req.Historico.TakeLast(6))
            {
                if (string.IsNullOrWhiteSpace(msg.Content)) continue;

                var role = string.Equals(msg.Role, "assistant", StringComparison.OrdinalIgnoreCase)
                    ? "assistant"
                    : "user";

                var texto = msg.Content.Trim();
                if (texto.Length > 1200)
                    texto = texto[..1200] + "...";

                messages.Add(new { role, content = texto });
            }
        }

        var userPrompt = MontarPromptUsuario(req.Pergunta, fragmentos, casosValidados, casual);
        userPrompt += usarRaciocinio ? "\n/think" : "\n/no_think";
        messages.Add(new { role = "user", content = userPrompt });

        var temperatura = string.Equals(req.ModoOperacao, "consulta_geral", StringComparison.OrdinalIgnoreCase)
            ? 0.45
            : 0.25;

        var payload = new
        {
            model = _llmModel,
            stream = true,
            think = usarRaciocinio,
            options = new
            {
                temperature = temperatura,
                num_ctx = 4096
            },
            messages
        };

        using var httpReq = new HttpRequestMessage(HttpMethod.Post, $"{_ollamaUrl}/api/chat")
        {
            Content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json")
        };

        using var httpResp = await _http.SendAsync(httpReq, HttpCompletionOption.ResponseHeadersRead, ct);
        httpResp.EnsureSuccessStatusCode();

        await using var stream = await httpResp.Content.ReadAsStreamAsync(ct);
        using var reader = new StreamReader(stream);

        while (!ct.IsCancellationRequested && await reader.ReadLineAsync(ct) is { } line)
        {
            if (string.IsNullOrWhiteSpace(line)) continue;

            string? token = null;
            bool done = false;

            using (var doc = JsonDocument.Parse(line))
            {
                if (doc.RootElement.TryGetProperty("message", out var msg) &&
                    msg.TryGetProperty("content", out var content))
                {
                    token = content.GetString();
                }

                if (doc.RootElement.TryGetProperty("done", out var doneEl))
                {
                    done = doneEl.GetBoolean();
                }
            }

            if (!string.IsNullOrEmpty(token))
                yield return token;

            if (done)
                break;
        }
    }

    public async Task<string> SalvarHistoricoAsync(
        ConsultaRequest req,
        string respostaCompleta,
        List<FragmentoDto> fontes,
        long tempoMs,
        CancellationToken ct)
    {
        var respostaLimpa = LimparPensamento(respostaCompleta);

        var resumoFontes = fontes.Select(f => new
        {
            f.NomeArquivo,
            f.CaminhoRelativo,
            f.Categoria,
            f.Subcategoria,
            f.Maquina,
            f.Versao,
            f.PaginaInicio,
            f.ScoreRrf
        });

        var docsJson = JsonSerializer.Serialize(resumoFontes);

        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);

        const string sql = """
            INSERT INTO historico_analises
                (usuario, modo_operacao, filtro_maquina, filtro_categoria, pergunta, resposta, documentos_utilizados, modelo_llm, tempo_resposta_ms)
            VALUES
                (@usuario, @modo, @maquina, @categoria, @pergunta, @resposta, @docs, @modelo, @tempo)
            RETURNING id::text;
            """;

        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("usuario", string.IsNullOrWhiteSpace(req.Usuario) ? "operador_local" : req.Usuario);
        cmd.Parameters.AddWithValue("modo", req.ModoOperacao);
        cmd.Parameters.AddWithValue("maquina", (object?)req.Maquina ?? DBNull.Value);
        cmd.Parameters.AddWithValue("categoria", (object?)req.Categoria ?? DBNull.Value);
        cmd.Parameters.AddWithValue("pergunta", req.Pergunta);
        cmd.Parameters.AddWithValue("resposta", respostaLimpa);
        cmd.Parameters.Add(new NpgsqlParameter("docs", NpgsqlDbType.Jsonb) { Value = docsJson });
        cmd.Parameters.AddWithValue("modelo", _llmModel);
        cmd.Parameters.AddWithValue("tempo", (int)tempoMs);

        var idObj = await cmd.ExecuteScalarAsync(ct);
        return idObj?.ToString() ?? string.Empty;
    }

    public async Task<bool> RegistrarAvaliacaoAsync(AvaliacaoRequest req, CancellationToken ct)
    {
        if (!Guid.TryParse(req.HistoricoId, out var idGuid))
            return false;

        var avaliacaoNorm = req.Avaliacao.Trim().ToUpperInvariant();
        if (avaliacaoNorm != "UTIL" && avaliacaoNorm != "RUIM")
            return false;

        var correcao = string.IsNullOrWhiteSpace(req.CorrecaoOperador) ? null : req.CorrecaoOperador.Trim();
        var aprovar = req.AprovarConhecimento ?? (avaliacaoNorm == "UTIL" || correcao != null);

        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);

        const string sql = """
            UPDATE historico_analises
            SET avaliacao = @avaliacao,
                correcao_operador = @correcao,
                aprovado_conhecimento = @aprovar,
                avaliado_em = NOW()
            WHERE id = @id;
            """;

        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("avaliacao", avaliacaoNorm);
        cmd.Parameters.AddWithValue("correcao", (object?)correcao ?? DBNull.Value);
        cmd.Parameters.AddWithValue("aprovar", aprovar);
        cmd.Parameters.AddWithValue("id", idGuid);

        return await cmd.ExecuteNonQueryAsync(ct) > 0;
    }

    public async Task<bool> AlternarAprovacaoAsync(string historicoId, bool aprovado, CancellationToken ct)
    {
        if (!Guid.TryParse(historicoId, out var idGuid))
            return false;

        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);

        const string sql = """
            UPDATE historico_analises
            SET aprovado_conhecimento = @aprovar,
                avaliado_em = COALESCE(avaliado_em, NOW())
            WHERE id = @id;
            """;

        await using var cmd = new NpgsqlCommand(sql, conn);
        cmd.Parameters.AddWithValue("aprovar", aprovado);
        cmd.Parameters.AddWithValue("id", idGuid);

        return await cmd.ExecuteNonQueryAsync(ct) > 0;
    }

    public async Task<FiltrosDisponiveisDto> ObterFiltrosAsync(CancellationToken ct)
    {
        var categorias = new List<string>();
        var maquinas = new List<string>();

        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);

        await using (var cmd = new NpgsqlCommand("SELECT DISTINCT categoria FROM documentos WHERE status = 'ATIVO' ORDER BY categoria", conn))
        await using (var reader = await cmd.ExecuteReaderAsync(ct))
        {
            while (await reader.ReadAsync(ct))
                categorias.Add(reader.GetString(0));
        }

        await using (var cmd = new NpgsqlCommand("SELECT DISTINCT maquina FROM documentos WHERE status = 'ATIVO' AND maquina IS NOT NULL ORDER BY maquina", conn))
        await using (var reader = await cmd.ExecuteReaderAsync(ct))
        {
            while (await reader.ReadAsync(ct))
                maquinas.Add(reader.GetString(0));
        }

        return new FiltrosDisponiveisDto(categorias, maquinas);
    }

    public async Task<List<DocumentoResumoDto>> ListarDocumentosAsync(CancellationToken ct)
    {
        var lista = new List<DocumentoResumoDto>();
        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);

        const string sql = """
            SELECT id::text, nome_arquivo, caminho_relativo, categoria, subcategoria,
                   maquina, versao, status, total_paginas, atualizado_em
            FROM documentos
            ORDER BY atualizado_em DESC
            LIMIT 200;
            """;

        await using var cmd = new NpgsqlCommand(sql, conn);
        await using var reader = await cmd.ExecuteReaderAsync(ct);

        while (await reader.ReadAsync(ct))
        {
            lista.Add(new DocumentoResumoDto(
                reader.GetString(0),
                reader.GetString(1),
                reader.GetString(2),
                reader.GetString(3),
                reader.IsDBNull(4) ? null : reader.GetString(4),
                reader.IsDBNull(5) ? null : reader.GetString(5),
                reader.GetInt32(6),
                reader.GetString(7),
                reader.IsDBNull(8) ? null : reader.GetInt32(8),
                reader.GetDateTime(9)
            ));
        }

        return lista;
    }

    public async Task<List<HistoricoItemDto>> ListarHistoricoAsync(CancellationToken ct)
    {
        var lista = new List<HistoricoItemDto>();
        await using var conn = new NpgsqlConnection(_dbDsn);
        await conn.OpenAsync(ct);

        const string sql = """
            SELECT id::text, usuario, modo_operacao, filtro_maquina, filtro_categoria,
                   pergunta, resposta, tempo_resposta_ms, criado_em,
                   avaliacao, correcao_operador, COALESCE(aprovado_conhecimento, FALSE)
            FROM historico_analises
            ORDER BY criado_em DESC
            LIMIT 80;
            """;

        await using var cmd = new NpgsqlCommand(sql, conn);
        await using var reader = await cmd.ExecuteReaderAsync(ct);

        while (await reader.ReadAsync(ct))
        {
            lista.Add(new HistoricoItemDto(
                reader.GetString(0),
                reader.GetString(1),
                reader.GetString(2),
                reader.IsDBNull(3) ? null : reader.GetString(3),
                reader.IsDBNull(4) ? null : reader.GetString(4),
                reader.GetString(5),
                LimparPensamento(reader.GetString(6)),
                reader.IsDBNull(7) ? null : reader.GetInt32(7),
                reader.GetDateTime(8),
                reader.IsDBNull(9) ? null : reader.GetString(9),
                reader.IsDBNull(10) ? null : reader.GetString(10),
                reader.GetBoolean(11)
            ));
        }

        return lista;
    }

    private static string MontarPromptSistema(string modoOperacao)
    {
        var baseConversacional = string.Join("\n", new[]
        {
            "Voc\u00ea \u00e9 um assistente t\u00e9cnico e parceiro de engenharia industrial rodando localmente na f\u00e1brica.",
            "Comporte-se de forma natural, cordial, clara e direta em portugu\u00eas, mantendo o contexto da conversa como um colega experiente.",
            "Diretrizes de intera\u00e7\u00e3o:",
            "1. Se o usu\u00e1rio cumprimentar, agradecer ou conversar normalmente, responda de forma natural e breve, sem citar documentos desnecessariamente.",
            "2. Entregue exatamente o que o usu\u00e1rio pedir (explica\u00e7\u00f5es, tabelas comparativas, checklists, resumos, rascunhos de relat\u00f3rio ou passo a passo).",
            "3. Quando houver trechos da biblioteca da f\u00e1brica anexados, use-os como fonte principal de verdade e cite a origem de forma natural, ex: [arquivo.pdf, p\u00e1g. 2].",
            "4. Quando houver 'APRENDIZADO OPERACIONAL' (casos anteriores validados ou corre\u00e7\u00f5es registradas pela equipe), leve essas corre\u00e7\u00f5es em alta considera\u00e7\u00e3o para evitar repetir erros passados, diferenciando claramente o que veio de manual oficial ([arquivo.pdf, p\u00e1g. X]) do que veio de experi\u00eancia de campo ([Hist\u00f3rico Validado]).",
            "5. Nunca invente valores num\u00e9ricos, setpoints, temperaturas ou especifica\u00e7\u00f5es de m\u00e1quinas da f\u00e1brica que n\u00e3o estejam nos documentos ou nas corre\u00e7\u00f5es validadas. Se faltar um dado espec\u00edfico, avise com transpar\u00eancia e, se ajudar, complemente com conhecimento geral de engenharia deixando claro que \u00e9 orienta\u00e7\u00e3o geral.",
            "6. Quando a solicita\u00e7\u00e3o estiver aberta ou faltar detalhe de m\u00e1quina/processo, responda da melhor forma e fa\u00e7a uma pergunta objetiva para ajudar o usu\u00e1rio a continuar."
        });

        var complemento = modoOperacao switch
        {
            "diagnostico_falha" => "\nFoco do modo atual (Diagn\u00f3stico de Falha): ao analisar um problema t\u00e9cnico, organize a resposta em Causa Prov\u00e1vel, Vari\u00e1veis Cr\u00edticas / Gargalos e A\u00e7\u00f5es Recomendadas, mantendo o tom conversacional.",
            "comparar_padrao_ouro" => "\nFoco do modo atual (Comparar Padr\u00e3o Ouro x Ocorr\u00eancias Ruins): compare os registros de 'padrao_ouro_boas' contra 'ocorrencias_ruins' e '03_parametros_processo', destacando desvios em tabela Markdown sempre que houver n\u00fameros.",
            _ => ""
        };

        return baseConversacional + complemento;
    }

    private static string MontarPromptUsuario(
        string pergunta,
        List<FragmentoDto> fragmentos,
        List<CasoValidadoDto> casosValidados,
        bool casual)
    {
        if (casual)
            return pergunta;

        if (fragmentos.Count == 0 && casosValidados.Count == 0)
        {
            return "[Nota do sistema: nenhum documento espec\u00edfico da biblioteca ou caso validado correspondeu aos filtros desta mensagem. Responda naturalmente usando o hist\u00f3rico da conversa ou conhecimento geral, avisando caso o usu\u00e1rio esteja pedindo um dado espec\u00edfico da f\u00e1brica.]\n\n" + pergunta;
        }

        var sb = new StringBuilder();

        if (fragmentos.Count > 0)
        {
            sb.AppendLine("[CONTEXTO RECUPERADO DA BIBLIOTECA DA F\u00c1BRICA - use se for relevante para a mensagem do usu\u00e1rio:]");
            for (int i = 0; i < fragmentos.Count; i++)
            {
                var f = fragmentos[i];
                sb.AppendLine($"--- FONTE {i + 1}: {f.NomeArquivo} | Categoria: {f.Categoria} | Subcategoria: {f.Subcategoria ?? "-"} | M\u00e1quina: {f.Maquina ?? "-"} | Vers\u00e3o: v{f.Versao} | P\u00e1gina: {f.PaginaInicio?.ToString() ?? "-"} ---");
                sb.AppendLine(f.ConteudoMarkdown);
                sb.AppendLine();
            }
        }

        if (casosValidados.Count > 0)
        {
            sb.AppendLine("[APRENDIZADO OPERACIONAL - CASOS E CORRE\u00c7\u00d5ES VALIDADAS ANTERIORMENTE PELA EQUIPE:]");
            for (int i = 0; i < casosValidados.Count; i++)
            {
                var c = casosValidados[i];
                sb.AppendLine($"--- HIST\u00d3RICO VALIDADO {i + 1} (Data: {c.CriadoEm:dd/MM/yyyy} | M\u00e1quina: {c.Maquina ?? "Geral"}) ---");
                sb.AppendLine($"Pergunta anterior: {c.Pergunta}");
                if (!string.IsNullOrWhiteSpace(c.CorrecaoOperador))
                {
                    sb.AppendLine($"CORRE\u00c7\u00c3O / ORIENTA\u00c7\u00c3O VALIDADA PELA EQUIPE (ALTA PRIORIDADE): {c.CorrecaoOperador}");
                }
                else
                {
                    var respCurta = c.Resposta.Length > 700 ? c.Resposta[..700] + "..." : c.Resposta;
                    sb.AppendLine($"Resposta anterior aprovada como \u00fatil: {respCurta}");
                }
                sb.AppendLine();
            }
        }

        sb.AppendLine("[MENSAGEM DO USU\u00c1RIO:]");
        sb.AppendLine(pergunta);
        return sb.ToString();
    }
}