using System.Diagnostics;
using IndustrialRag.Api.Models;
using IndustrialRag.Api.Services;
using Microsoft.AspNetCore.SignalR;

namespace IndustrialRag.Api.Hubs;

public class ChatHub : Hub
{
    private readonly RagOrchestratorService _orchestrator;
    private readonly AuthService _auth;

    public ChatHub(RagOrchestratorService orchestrator, AuthService auth)
    {
        _orchestrator = orchestrator;
        _auth = auth;
    }

    public async Task EnviarPergunta(ConsultaRequest req)
    {
        var ct = Context.ConnectionAborted;
        var sw = Stopwatch.StartNew();
        var modeloAtivo = _orchestrator.ResolverModelo(req.ModeloLlm);

        try
        {
            var user = await _auth.ValidarTokenAsync(req.AuthToken, ct);
            if (user == null)
            {
                await Clients.Caller.SendAsync("ErroConsulta", "Sess\u00e3o expirada. Fa\u00e7a login novamente.", ct);
                return;
            }

            req.Usuario = user.Username;
            await Clients.Caller.SendAsync("StatusAtualizado", $"Agente analisando ({modeloAtivo})...", ct);

            var (sessaoId, sessaoTitulo) = await _orchestrator.GarantirOuCriarSessaoAsync(user.Id, req.SessaoId, req.Pergunta, ct);

            var resultado = await _orchestrator.ExecutarAgenteAsync(
                req,
                status => Clients.Caller.SendAsync("StatusAtualizado", status, ct),
                ct);

            sw.Stop();
            var historicoId = await _orchestrator.SalvarHistoricoComSessaoAsync(
                req, resultado.Resposta, resultado.Fontes, sw.ElapsedMilliseconds, modeloAtivo, user.Id, sessaoId, ct);

            await Clients.Caller.SendAsync("FinalizarResposta", new
            {
                resposta = resultado.Resposta,
                fontes = resultado.Fontes,
                casosValidados = resultado.CasosValidados,
                ferramentasUsadas = resultado.FerramentasUsadas,
                historicoId,
                sessaoId,
                sessaoTitulo,
                tempoMs = sw.ElapsedMilliseconds,
                modelo = modeloAtivo
            }, ct);
        }
        catch (Exception ex)
        {
            await Clients.Caller.SendAsync("ErroConsulta", $"Falha no agente ({modeloAtivo}): {ex.Message}", ct);
        }
    }
}