using System.Diagnostics;
using System.Text;
using IndustrialRag.Api.Models;
using IndustrialRag.Api.Services;
using Microsoft.AspNetCore.SignalR;

namespace IndustrialRag.Api.Hubs;

public class ChatHub : Hub
{
    private readonly RagOrchestratorService _orchestrator;

    public ChatHub(RagOrchestratorService orchestrator)
    {
        _orchestrator = orchestrator;
    }

    public async Task EnviarPergunta(ConsultaRequest req)
    {
        var ct = Context.ConnectionAborted;
        var sw = Stopwatch.StartNew();
        var modeloAtivo = _orchestrator.ResolverModelo(req.ModeloLlm);

        try
        {
            await Clients.Caller.SendAsync("StatusAtualizado", $"Gerando resposta ({modeloAtivo})...", ct);

            var casual = _orchestrator.EhConversaCasual(req.Pergunta);
            List<FragmentoDto> fontes = [];
            List<CasoValidadoDto> casosValidados = [];

            if (!casual)
            {
                var tarefaFontes = _orchestrator.BuscarContextoAsync(req, ct);
                var tarefaCasos = _orchestrator.BuscarCasosValidadosAsync(req, ct);
                await Task.WhenAll(tarefaFontes, tarefaCasos);
                fontes = tarefaFontes.Result;
                casosValidados = tarefaCasos.Result;
            }

            var respostaCompleta = new StringBuilder();
            await foreach (var token in _orchestrator.GerarRespostaStreamAsync(req, fontes, casosValidados, ct))
            {
                respostaCompleta.Append(token);
                await Clients.Caller.SendAsync("ReceberToken", token, ct);
            }

            sw.Stop();
            var textoFinal = RagOrchestratorService.LimparPensamento(respostaCompleta.ToString());
            var historicoId = await _orchestrator.SalvarHistoricoAsync(req, textoFinal, fontes, sw.ElapsedMilliseconds, modeloAtivo, ct);

            await Clients.Caller.SendAsync("FinalizarResposta", new
            {
                resposta = textoFinal,
                fontes,
                casosValidados,
                historicoId,
                tempoMs = sw.ElapsedMilliseconds,
                modelo = modeloAtivo
            }, ct);
        }
        catch (Exception ex)
        {
            await Clients.Caller.SendAsync("ErroConsulta", $"Falha no processamento ({modeloAtivo}): {ex.Message}", ct);
        }
    }
}