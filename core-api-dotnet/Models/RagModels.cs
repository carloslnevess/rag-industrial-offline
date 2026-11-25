using System.Text.Json.Serialization;

namespace IndustrialRag.Api.Models;

public class MensagemHistorico
{
    public string Role { get; set; } = "user";
    public string Content { get; set; } = string.Empty;
}

public class LoginRequest
{
    public string Username { get; set; } = string.Empty;
    public string Senha { get; set; } = string.Empty;
}

public record UsuarioAutenticadoDto(string Id, string Username, string NomeExibicao, string Perfil);
public record AuthRespostaDto(string Token, UsuarioAutenticadoDto Usuario);
public record SessaoResumoDto(string Id, string Titulo, DateTime AtualizadoEm);

public record MensagemSessaoDto(
    string HistoricoId, string Pergunta, string Resposta,
    int? TempoRespostaMs, string? ModeloLlm, string? Avaliacao,
    string? CorrecaoOperador, DateTime CriadoEm);

public class BuscaPythonRequest
{
    [JsonPropertyName("query")] public string Query { get; set; } = string.Empty;
    [JsonPropertyName("categoria")] public string? Categoria { get; set; }
    [JsonPropertyName("subcategoria")] public string? Subcategoria { get; set; }
    [JsonPropertyName("maquina")] public string? Maquina { get; set; }
    [JsonPropertyName("top_k")] public int TopK { get; set; } = 6;
}

public class FragmentoDto
{
    [JsonPropertyName("fragmento_id")] public string FragmentoId { get; set; } = string.Empty;
    [JsonPropertyName("documento_id")] public string DocumentoId { get; set; } = string.Empty;
    [JsonPropertyName("nome_arquivo")] public string NomeArquivo { get; set; } = string.Empty;
    [JsonPropertyName("caminho_relativo")] public string CaminhoRelativo { get; set; } = string.Empty;
    [JsonPropertyName("categoria")] public string Categoria { get; set; } = string.Empty;
    [JsonPropertyName("subcategoria")] public string? Subcategoria { get; set; }
    [JsonPropertyName("maquina")] public string? Maquina { get; set; }
    [JsonPropertyName("versao")] public int Versao { get; set; }
    [JsonPropertyName("pagina_inicio")] public int? PaginaInicio { get; set; }
    [JsonPropertyName("conteudo_markdown")] public string ConteudoMarkdown { get; set; } = string.Empty;
    [JsonPropertyName("score_rrf")] public double ScoreRrf { get; set; }
}

public class ConsultaRequest
{
    public string Pergunta { get; set; } = string.Empty;
    public string ModoOperacao { get; set; } = "consulta_geral";
    public string? Categoria { get; set; }
    public string? Maquina { get; set; }
    public string Usuario { get; set; } = "operador_local";
    public string? ModeloLlm { get; set; }
    public string? SessaoId { get; set; }
    public string? AuthToken { get; set; }
    public bool RaciocinioProfundo { get; set; } = false;
    public int TopK { get; set; } = 5;
    public List<MensagemHistorico> Historico { get; set; } = [];
}

public class AvaliacaoRequest
{
    public string HistoricoId { get; set; } = string.Empty;
    public string Avaliacao { get; set; } = string.Empty;
    public string? CorrecaoOperador { get; set; }
    public bool? AprovarConhecimento { get; set; }
}

public class AprovacaoRequest { public bool Aprovado { get; set; } }

public record ModeloOllamaDto(string Nome, double TamanhoGb, string? Parametros, bool EhPadrao);

public record CasoValidadoDto(
    string Id, string Pergunta, string Resposta, string? CorrecaoOperador,
    string? Maquina, string? Categoria, DateTime CriadoEm, double Score);

public record AgenteResultadoDto(
    string Resposta, List<FragmentoDto> Fontes, List<CasoValidadoDto> CasosValidados, List<string> FerramentasUsadas);

public record ConsultaRespostaDto(
    string Resposta, List<FragmentoDto> Fontes, long TempoRespostaMs, string ModeloLlm,
    string? HistoricoId, string? SessaoId, string? SessaoTitulo,
    List<CasoValidadoDto> CasosValidados, List<string> FerramentasUsadas);

public record DocumentoResumoDto(
    string Id, string NomeArquivo, string CaminhoRelativo, string Categoria,
    string? Subcategoria, string? Maquina, int Versao, string Status, int? TotalPaginas, DateTime AtualizadoEm);

public record HistoricoItemDto(
    string Id, string Usuario, string ModoOperacao, string? FiltroMaquina, string? FiltroCategoria,
    string Pergunta, string Resposta, int? TempoRespostaMs, DateTime CriadoEm,
    string? Avaliacao, string? CorrecaoOperador, bool AprovadoConhecimento);

public record FiltrosDisponiveisDto(List<string> Categorias, List<string> Maquinas);