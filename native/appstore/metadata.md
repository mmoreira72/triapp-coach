# Tri Squad: ficha da App Store

Tudo o que o App Store Connect pede para submeter a versão 1.0 (App Store Connect → Tri Squad → App Store → versão 1.0).
Os limites de caracteres da Apple estão entre parênteses.

## Informação da app

| Campo | Valor |
|---|---|
| Nome (30) | Tri Squad |
| Subtítulo (30) | Triatlo com o teu treinador |
| Categoria principal | Saúde e fitness |
| Categoria secundária | Desporto |
| Classificação etária | 4+ (responder "Nenhum" a todas as perguntas) |
| URL de suporte | https://tri.laloop.ai/suporte |
| URL de marketing | https://tri.laloop.ai/sobre |
| URL da política de privacidade | https://tri.laloop.ai/privacidade |
| Copyright | 2026 Tri Squad |
| Preço | Gratuito |

## Texto promocional (170)

O treino de hoje explicado em linguagem simples, todas as manhãs: o plano do treinador, o que fizeste ontem e como estás de forma e recuperação.

## Descrição (4000)

O Tri Squad é a app de treino de um grupo de triatlo. Junta o plano do treinador (TrainingPeaks) com o que fazes no relógio (Garmin e outros, através do Intervals.icu, ou o Apple Watch) e explica tudo em linguagem simples.

TODAS AS MANHÃS
• O treino de hoje, com a descrição do treinador e para que serve na tua preparação.
• Um relatório do dia anterior: o que fizeste contra o planeado, como mudou a tua forma e como dormiste e recuperaste.
• Uma notificação às 7h30 com o resumo, e widgets no ecrã inicial e no ecrã bloqueado com a forma, o treino de hoje e de amanhã, a data e o pôr do sol.

CADA TREINO
• O tipo de treino calculado pelo tempo em cada zona de frequência cardíaca ou potência.
• Planeado contra executado, bloco a bloco.
• Treinos falhados: dizes ao treinador o que aconteceu com dois toques.

ANÁLISE
• Forma (CTL), fadiga (ATL) e frescura (TSB), carga semanal e consistência.
• Curva potência-duração com mFTP, TTE, FRC e Pmax.
• Regras de treino aplicadas à tua semana, com alertas quando algo precisa de atenção.
• Um assistente que responde a perguntas sobre os teus dados, mesmo sem rede, com o modelo de linguagem do iPhone.

PROVAS
• Previsão de tempo para a prova-alvo, segmento a segmento, com o percurso, o vento e o teu histórico.
• Relatório de cada prova, do atleta e do treinador.

RECUPERAÇÃO
• Sono, FC em repouso e HRV do Apple Health (só com a tua autorização e só leitura) ou do teu relógio através do Intervals.icu.

COMO FUNCIONA
Crias a tua conta com email e palavra-passe e o teu treinador associa-a aos teus dados do TrainingPeaks. Depois ligas o teu calendário do TrainingPeaks e o Intervals.icu na página Ligações, com instruções passo a passo. Os teus dados ficam no servidor do grupo e só tu e o teu treinador os veem.

## Palavras-chave (100)

triatlo,treino,trainingpeaks,intervals,ctl,tss,ftp,ciclismo,corrida,natação,treinador,garmin

## Novidades desta versão

Primeira versão.

## Capturas de ecrã

iPhone 6,9" (1320×2868): `screenshots/iphone69-1.png` a `iphone69-5.png`, por esta ordem. A app é só para iPhone, por isso
não são precisas capturas de iPad. Para as refazer: `TRIAPP_OUT=<pasta com data/> TRIAPP_SECRET=x python native/appstore/make_screenshots.py native/appstore/screenshots`.

## Informação para a revisão da Apple

| Campo | Valor |
|---|---|
| Início de sessão necessário | Sim |
| Utilizador | demo@laloop.ai |
| Palavra-passe | (a da conta de demonstração, guardada fora do repositório) |
| Contacto | o teu nome, telefone e email |

Notas para o revisor (copiar):

> Tri Squad is the training app of a triathlon squad. Athletes create their account in the app ("Cria a tua conta" on the sign-in
> screen); the squad's coach then associates it with the athlete's TrainingPeaks data, so a brand-new account only shows a waiting
> page. Please use the demo athlete above to see the app with data: it is an anonymised copy of a real athlete.
> - Treinos tab: today's planned session and the morning report, each session with its training type, missed sessions, races and race reports.
> - Análise tab: training-rule findings and an assistant that answers questions about the athlete's data.
> - Dados de treino and Prova-alvo tabs: load, form and power curves, and the race prediction.
> - HealthKit: the app only READS sleep, resting heart rate and HRV, after the user allows it, to show recovery next to training load. It never writes to Health, and health data is not used for advertising or shared with third parties.
> - Push notifications: one morning summary per day and alerts from the coach's rules; quiet hours 22:00-07:30.
> - Widgets: form, today's and tomorrow's session, date and sunset.
> - On-device language model (Apple Foundation Models): answers the assistant's questions when the server cannot be reached.
> - Account deletion: Ligações (top of the page) → "Apagar a minha conta".

## Privacidade da app (questionário "App Privacy")

Rastreio (tracking): **Não**. Dados recolhidos, todos **ligados à identidade** do utilizador, **não usados para rastreio**,
finalidade **Funcionalidade da app**:

| Tipo de dados | Recolhido | Notas |
|---|---|---|
| Informação de contacto → Nome | Sim | dado pelo atleta ao criar a conta |
| Informação de contacto → Endereço de email | Sim | login |
| Saúde e fitness → Saúde | Sim | sono, FC em repouso, HRV (Apple Health ou Intervals.icu) |
| Saúde e fitness → Fitness | Sim | treinos, FC, potência, ritmo, distância |
| Conteúdo do utilizador → Outro conteúdo | Sim | notas, motivos de treinos falhados, relatórios de prova, perguntas ao assistente |
| Localização | Não | (os ficheiros de treino podem ter GPS, mas a app não pede a localização do aparelho; se o revisor perguntar: os percursos ficam só no servidor do grupo) |
| Identificadores, Utilização, Diagnóstico, Compras, Dados financeiros | Não | |
