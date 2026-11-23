using System.Diagnostics;
using System.Text;
using IndustrialRag.Api.Hubs;
using IndustrialRag.Api.Models;
using IndustrialRag.Api.Services;

var builder = WebApplication.CreateBuilder(args);

builder.WebHost.UseUrls("http://0.0.0.0:5000");

builder.Services.AddHttpClient<RagOrchestratorService>();
builder.Services.AddSignalR();
builder.Services.AddCors(options =>
{
    options.AddDefaultPolicy(policy =>
        policy.AllowAnyHeader()
              .AllowAnyMethod()
              .SetIsOriginAllowed(_ => true)
              .AllowCredentials());
});

var app = builder.Build();

try
{
    using var scope = app.Services.CreateScope();
    var ragInit = scope.ServiceProvider.GetRequiredService<RagOrchestratorService>();
    await ragInit.GarantirSchemaAprendizadoAsync();
}
catch (Exception ex)
{
    Console.WriteLine($"[AVISO] Schema de aprendizado sera validado depois: {ex.Message}");
}

app.UseCors();
app.UseDefaultFiles();
app.UseStaticFiles();

app.MapGet("/api/health", () => Results.Ok(new { status = "ok", servico = "core-api-dotnet" }));

app.MapGet("/api/modelos", async (RagOrchestratorService rag, CancellationToken ct) =>
    Results.Ok(await rag.ListarModelosOllamaAsync(ct)));

app.MapGet("/api/filtros", async (RagOrchestratorService rag, CancellationToken ct) =>
    Results.Ok(await rag.ObterFiltrosAsync(ct)));

app.MapGet("/api/documentos", async (RagOrchestratorService rag, CancellationToken ct) =>
    Results.Ok(await rag.ListarDocumentosAsync(ct)));

app.MapGet("/api/historico", async (RagOrchestratorService rag, CancellationToken ct) =>
    Results.Ok(await rag.ListarHistoricoAsync(ct)));

app.MapPost("/api/avaliacao", async (AvaliacaoRequest req, RagOrchestratorService rag, CancellationToken ct) =>
{
    var ok = await rag.RegistrarAvaliacaoAsync(req, ct);
    return ok
        ? Results.Ok(new { status = "salvo" })
        : Results.BadRequest("ID ou avalia\u00e7\u00e3o inv\u00e1lida.");
});

app.MapPost("/api/historico/{id}/aprovacao", async (string id, AprovacaoRequest req, RagOrchestratorService rag, CancellationToken ct) =>
{
    var ok = await rag.AlternarAprovacaoAsync(id, req.Aprovado, ct);
    return ok
        ? Results.Ok(new { status = "atualizado", aprovado = req.Aprovado })
        : Results.BadRequest("ID inv\u00e1lido.");
});

app.MapPost("/api/chat", async (ConsultaRequest req, RagOrchestratorService rag, CancellationToken ct) =>
{
    if (string.IsNullOrWhiteSpace(req.Pergunta))
        return Results.BadRequest("A pergunta \u00e9 obrigat\u00f3ria.");

    var modeloAtivo = rag.ResolverModelo(req.ModeloLlm);
    var sw = Stopwatch.StartNew();
    var tarefaFontes = rag.BuscarContextoAsync(req, ct);
    var tarefaCasos = rag.BuscarCasosValidadosAsync(req, ct);
    await Task.WhenAll(tarefaFontes, tarefaCasos);

    var fontes = tarefaFontes.Result;
    var casosValidados = tarefaCasos.Result;

    var sb = new StringBuilder();
    await foreach (var token in rag.GerarRespostaStreamAsync(req, fontes, casosValidados, ct))
        sb.Append(token);

    sw.Stop();
    var resposta = RagOrchestratorService.LimparPensamento(sb.ToString());
    var historicoId = await rag.SalvarHistoricoAsync(req, resposta, fontes, sw.ElapsedMilliseconds, modeloAtivo, ct);

    return Results.Ok(new ConsultaRespostaDto(resposta, fontes, sw.ElapsedMilliseconds, modeloAtivo, historicoId, casosValidados));
});

app.MapHub<ChatHub>("/hubs/chat");

app.Run();