using System.Diagnostics;
using IndustrialRag.Api.Hubs;
using IndustrialRag.Api.Models;
using IndustrialRag.Api.Services;

var builder = WebApplication.CreateBuilder(args);
builder.WebHost.UseUrls("http://0.0.0.0:5000");

builder.Services.AddHttpClient<RagOrchestratorService>();
builder.Services.AddSingleton<AuthService>();
builder.Services.AddSignalR();
builder.Services.AddCors(o => o.AddDefaultPolicy(p => p.AllowAnyHeader().AllowAnyMethod().SetIsOriginAllowed(_ => true).AllowCredentials()));

var app = builder.Build();

try
{
    using var scope = app.Services.CreateScope();
    await scope.ServiceProvider.GetRequiredService<RagOrchestratorService>().GarantirSchemaAprendizadoAsync();
    await scope.ServiceProvider.GetRequiredService<AuthService>().GarantirSchemaAuthAsync();
}
catch (Exception ex) { Console.WriteLine($"[AVISO] Schema: {ex.Message}"); }

app.UseCors();
app.UseDefaultFiles();
app.UseStaticFiles(new StaticFileOptions
{
    OnPrepareResponse = ctx =>
    {
        ctx.Context.Response.Headers["Cache-Control"] = "no-cache, no-store, must-revalidate";
        ctx.Context.Response.Headers["Pragma"] = "no-cache";
        ctx.Context.Response.Headers["Expires"] = "0";
    }
});

app.MapGet("/api/health", () => Results.Ok(new { status = "ok", servico = "core-api-dotnet" }));

app.MapPost("/api/auth/login", async (LoginRequest req, AuthService auth, CancellationToken ct) =>
{
    var (ok, erro, dados) = await auth.LoginAsync(req, ct);
    return ok ? Results.Ok(dados) : Results.Json(new { erro }, statusCode: 401);
});

app.MapGet("/api/auth/me", async (HttpContext ctx, AuthService auth, CancellationToken ct) =>
{
    var user = await auth.ValidarTokenAsync(AuthService.ExtrairToken(ctx), ct);
    return user != null ? Results.Ok(user) : Results.Unauthorized();
});

app.MapPost("/api/auth/logout", async (HttpContext ctx, AuthService auth, CancellationToken ct) =>
{
    await auth.LogoutAsync(AuthService.ExtrairToken(ctx), ct);
    return Results.Ok(new { status = "desconectado" });
});

app.MapGet("/api/sessoes", async (HttpContext ctx, AuthService auth, RagOrchestratorService rag, CancellationToken ct) =>
{
    var user = await auth.ValidarTokenAsync(AuthService.ExtrairToken(ctx), ct);
    return user == null ? Results.Unauthorized() : Results.Ok(await rag.ListarSessoesUsuarioAsync(user.Id, ct));
});

app.MapGet("/api/sessoes/{id}/mensagens", async (string id, HttpContext ctx, AuthService auth, RagOrchestratorService rag, CancellationToken ct) =>
{
    var user = await auth.ValidarTokenAsync(AuthService.ExtrairToken(ctx), ct);
    return user == null ? Results.Unauthorized() : Results.Ok(await rag.ObterMensagensSessaoAsync(user.Id, id, ct));
});

app.MapDelete("/api/sessoes/{id}", async (string id, HttpContext ctx, AuthService auth, RagOrchestratorService rag, CancellationToken ct) =>
{
    var user = await auth.ValidarTokenAsync(AuthService.ExtrairToken(ctx), ct);
    if (user == null) return Results.Unauthorized();
    return await rag.ExcluirSessaoAsync(user.Id, id, ct) ? Results.Ok(new { status = "excluida" }) : Results.NotFound();
});

app.MapGet("/api/modelos", async (RagOrchestratorService rag, CancellationToken ct) => Results.Ok(await rag.ListarModelosOllamaAsync(ct)));
app.MapGet("/api/filtros", async (RagOrchestratorService rag, CancellationToken ct) => Results.Ok(await rag.ObterFiltrosAsync(ct)));
app.MapGet("/api/documentos", async (RagOrchestratorService rag, CancellationToken ct) => Results.Ok(await rag.ListarDocumentosAsync(ct)));
app.MapGet("/api/historico", async (RagOrchestratorService rag, CancellationToken ct) => Results.Ok(await rag.ListarHistoricoAsync(ct)));

app.MapPost("/api/avaliacao", async (AvaliacaoRequest req, RagOrchestratorService rag, CancellationToken ct) =>
    await rag.RegistrarAvaliacaoAsync(req, ct) ? Results.Ok(new { status = "salvo" }) : Results.BadRequest());

app.MapPost("/api/historico/{id}/aprovacao", async (string id, AprovacaoRequest req, RagOrchestratorService rag, CancellationToken ct) =>
    await rag.AlternarAprovacaoAsync(id, req.Aprovado, ct) ? Results.Ok(new { status = "atualizado" }) : Results.BadRequest());

app.MapPost("/api/chat", async (ConsultaRequest req, HttpContext ctx, AuthService auth, RagOrchestratorService rag, CancellationToken ct) =>
{
    var user = await auth.ValidarTokenAsync(AuthService.ExtrairToken(ctx) ?? req.AuthToken, ct);
    if (user == null) return Results.Unauthorized();
    if (string.IsNullOrWhiteSpace(req.Pergunta)) return Results.BadRequest("Pergunta obrigat\u00f3ria.");

    req.Usuario = user.Username;
    var (sessaoId, sessaoTitulo) = await rag.GarantirOuCriarSessaoAsync(user.Id, req.SessaoId, req.Pergunta, ct);
    var modeloAtivo = rag.ResolverModelo(req.ModeloLlm);
    var sw = Stopwatch.StartNew();
    var res = await rag.ExecutarAgenteAsync(req, null, ct);
    sw.Stop();
    var histId = await rag.SalvarHistoricoComSessaoAsync(req, res.Resposta, res.Fontes, sw.ElapsedMilliseconds, modeloAtivo, user.Id, sessaoId, ct);

    return Results.Ok(new ConsultaRespostaDto(res.Resposta, res.Fontes, sw.ElapsedMilliseconds, modeloAtivo, histId, sessaoId, sessaoTitulo, res.CasosValidados, res.FerramentasUsadas));
});

app.MapHub<ChatHub>("/hubs/chat");
app.Run();