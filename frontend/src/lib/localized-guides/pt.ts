import type { TranslatedGuideBundle } from "./types";

export const ptGuideBundle: TranslatedGuideBundle = {
  locale: "pt",
  shell: {
    seeHostedPlans: "Ver planos hospedados",
    faqTocLabel: "Perguntas frequentes",
  },
  clipExamplesChrome: {
    eyebrow: "Feito com o SupoClip",
    fullClipMeta: (durationSeconds, sourceRange) => `Corte completo de ${durationSeconds}s · trecho original ${sourceRange}`,
    videoAriaLabel: (clipTitle, sourceTitle) => `${clipTitle} — exemplo do SupoClip extraído de ${sourceTitle}`,
    sourcePrefix: "Fonte:",
    watchOriginal: "Assistir ao momento original",
    detailsSummary: "Fonte, licença e edições",
    viewsNote: (formattedViews, checkedAt) => `${formattedViews} visualizações no YouTube quando selecionado em ${checkedAt}.`,
    licenseRecord: "Registro de fonte e licença",
    noEndorsement: "Estes exemplos editoriais não implicam endosso por parte dos participantes nem de quem publicou o vídeo original.",
    downloadAttribution: "Baixar atribuição e notas de processamento",
    footerNote:
      "Estes são cortes de exemplo completos, codificados em 540×960 para esta página. Os timestamps de origem se referem à gravação original; pausas removidas podem deixar o corte final mais curto que esse intervalo.",
  },
  gta6: {
    post: {
      title: "Como fazer cortes de GTA 6 (e quanto isso realmente paga)",
      description:
        "Transforme lives e VODs de GTA 6 em TikToks e Shorts: quanto pagam as campanhas de cortes, as regras da Rockstar Games e o fluxo passo a passo do VOD ao vídeo vertical legendado.",
      eyebrow: "Cortes de GTA 6",
      category: "Guia",
      readingTime: "Leitura de 10 min",
      summary:
        "Todo mundo está fazendo cortes de GTA 6. Veja quanto isso paga, quais cortes caem por direitos ou spoilers e o passo a passo para transformar uma live de quatro horas em vídeos verticais que valem o clique.",
      keywords: [
        "como fazer cortes de GTA 6",
        "cortes de GTA 6",
        "ganhar dinheiro com cortes de GTA 6",
        "campanha de cortes GTA 6",
        "clips GTA 6 TikTok",
        "cortar live de GTA 6",
      ],
      imageAlt: "Ilustração de uma live ao pôr do sol no estilo GTA com webcam no canto se transformando em três cortes verticais legendados",
    },
    breadcrumb: "Como fazer cortes de GTA 6",
    toc: {
      whyNow: "Por que GTA 6",
      whatItPays: "Quanto paga",
      rules: "As regras",
      workflow: "O fluxo",
      launchWeek: "Semana de lançamento",
      mistakes: "Erros comuns",
      faq: "Perguntas frequentes",
    },
    faqs: [
      {
        question: "Dá mesmo para ganhar dinheiro fazendo cortes de GTA 6?",
        answer:
          "Alguns editores de cortes ganham, principalmente em campanhas que pagam por 1.000 visualizações aprovadas, geralmente em torno de US$ 1. A maioria dos cortes rende bem pouco e poucos carregam o resultado do mês, os orçamentos das campanhas acabam e muitas colocam um teto por corte. Encare como prática remunerada de edição, não como renda garantida.",
      },
      {
        question: "É permitido postar cortes de GTA 6?",
        answer:
          "A política de vídeos da Rockstar Games permite vídeos de fãs para uso não comercial e reserva à Take-Two o direito de remover qualquer conteúdo a qualquer momento. Ela mira especialmente o final do jogo, grandes revelações da história, cutscenes isoladas e imagens vazadas antes do lançamento. A monetização não é concedida explicitamente, então leia a política e as regras da sua campanha antes de contar com o dinheiro.",
      },
      {
        question: "O SupoClip consegue cortar uma live de GTA 6 enquanto ela ainda está ao vivo?",
        answer:
          "Não. O SupoClip trabalha com vídeo gravado: um VOD ou clipe da Twitch ou da Kick, um vídeo do YouTube ou um arquivo que você enviar. Faça os cortes do VOD assim que a live terminar.",
      },
      {
        question: "A IA vai encontrar todos os bons momentos de GTA 6?",
        answer:
          "Não. O SupoClip seleciona momentos a partir do que é falado, então perseguições narradas, reações e opiniões entram na lista sugerida. Uma quase batida silenciosa ou um bug de física engraçado pode passar batido. Anote esses timestamps enquanto assiste e recorte manualmente.",
      },
      {
        question: "Quantos cortes de GTA 6 eu tiro de uma única live?",
        answer:
          "A lista sugerida do SupoClip cresce conforme a duração do vídeo, em torno de um candidato a cada cinco minutos até um limite. Fique só com os trechos que funcionam sozinhos; poucos cortes bem acabados rendem mais do que vinte feitos às pressas.",
      },
      {
        question: "E as músicas da rádio dentro do jogo?",
        answer:
          "As estações de rádio do GTA tocam músicas licenciadas. Elas podem deixar o corte sem som ou gerar reivindicação de direitos autorais no TikTok e no YouTube, algo separado da política da Rockstar Games. Corte evitando as músicas ou abaixe o volume da rádio sempre que puder.",
      },
    ],
    cta: {
      title: "Faça os cortes da sua primeira live de GTA 6",
      body: "Cole um VOD finalizado da Twitch, da Kick ou do YouTube. Revise a lista sugerida, ajuste o enquadramento e as legendas e poste os cortes que você mesmo assistiria até o fim.",
      label: "Cortar um VOD de GTA 6",
    },
    heroCaption: "Ilustração original. O SupoClip não é afiliado à Rockstar Games nem à Take-Two Interactive.",
    intro: [
      "Seu feed está cheio deles: o rosto do streamer em cima, uma perseguição de GTA embaixo, legendas pulando palavra por palavra e alguém nos comentários perguntando quanto o editor de cortes ganhou. Fazer cortes de GTA 6 é a aposta óbvia da temporada, e sim, paga algumas pessoas — paga quem trata isso como edição de verdade, não como bilhete de loteria.",
      "Este guia mostra o fluxo completo: de onde vêm os cortes, quanto as campanhas realmente pagam, quais regras derrubam vídeos e como sair de um VOD de quatro horas para alguns cortes verticais prontos no SupoClip. As ferramentas visuais ao longo do guia são interativas; teste com os seus próprios números.",
    ],
    whyNow: {
      heading: "Por que todo mundo está fazendo cortes de GTA 6 agora",
      p1LinkText: "Grand Theft Auto VI",
      p1AfterLinkBeforeDate: " chega em ",
      p1Date: "19 de novembro de 2026",
      p1AfterDate:
        ", para PlayStation 5 e Xbox Series X|S, com versão de PC planejada para depois. O jogo se passa no estado de Leonida, nos arredores de Vice City, e acompanha Lucia e Jason. A semana de lançamento tem tudo para ser uma das mais assistidas do ano nas lives, e a maioria das pessoas vai ver isso em cortes curtos, não em VODs de quatro horas.",
      p2BeforeStrong: "Essa é a oportunidade em uma frase: ",
      p2Strong: "as lives são longas, mas os feeds querem 30 a 60 segundos",
      p2AfterStrong:
        ". Alguém precisa encontrar o momento certo, cortar de um jeito que um desconhecido entenda na hora, enquadrar para celular e colocar legendas. Streamers, marcas e agências pagam editores de cortes para fazer isso, e quem joga ao vivo faz o mesmo para crescer o próprio canal.",
    },
    whatItPays: {
      heading: "Quanto realmente paga fazer cortes de GTA 6",
      lead: "Existem duas formas de transformar cortes de GTA 6 em dinheiro, e elas funcionam de jeitos bem diferentes.",
      li1Label: "Campanhas de cortes.",
      li1BeforeLink: " Um streamer ou marca coloca um orçamento em um marketplace como o ",
      li1LinkText: "Whop Content Rewards",
      li1AfterLink:
        ", define um valor por 1.000 visualizações e publica as regras de uso do conteúdo. Você posta os cortes nas suas contas, envia os links e recebe pelas visualizações aprovadas na revisão. A Whop aponta uma média em torno de US$ 1 a cada 1.000 visualizações; cada campanha de GTA define seu próprio valor, muitas vezes com um teto por corte, e para de pagar quando o orçamento acaba.",
      li2Label: "Seu próprio canal.",
      li2BeforeLink:
        " Se você mesmo joga e comenta, os cortes levam público para suas lives e podem monetizar pelos programas das plataformas. As ",
      li2LinkText: "políticas de monetização do YouTube",
      li2AfterLink:
        " excluem conteúdo reutilizado sem comentário ou edição relevante, então apenas repostar gameplay de outra pessoa cru não é um bom plano.",
      mathIntro:
        "A conta das campanhas é simples e pé no chão. A US$ 1 por 1.000 visualizações, 100.000 visualizações aprovadas rendem US$ 100. Teste os seus números abaixo, incluindo o custo do plano do SupoClip de que você precisaria:",
    },
    rules: {
      heading: "As regras que fazem cortes de GTA 6 serem derrubados",
      p1BeforeLink: "A Rockstar Games publica uma ",
      p1LinkText: "política sobre a publicação de material protegido por direitos autorais",
      p1BetweenLinkAndEm: ". Ela permite vídeos de fãs para uso ",
      p1Em: "não comercial",
      p1AfterEm:
        ", definido como não lucrar diretamente com as imagens, e mantém o direito de solicitar a remoção de qualquer vídeo. O texto cita especificamente vídeos do final ou de grandes revelações da história, compilações editadas de cutscenes e gravações anteriores ao lançamento. Cutscenes exibidas dentro de uma gameplay narrada mais longa costumam ser aceitas.",
      p2: "Além disso, cada campanha tem suas próprias regras: quais lives você pode usar, tags ou links obrigatórios, duração mínima do corte e proibição de artifícios como compra de visualizações. Quebrou uma regra, as visualizações não contam. Leia tudo antes de começar a cortar, nunca depois.",
    },
    workflow: {
      heading: "O fluxo: de um VOD de GTA 6 até os cortes prontos",
      lead: "Veja o processo inteiro de uma vez. A divisão de tarefas importa: o SupoClip faz o trabalho repetitivo (transcrever, sugerir trechos, enquadrar e sincronizar legendas) para que sua atenção fique nas decisões que só você pode tomar.",
      s1Heading: "1. Escolha um VOD que você tenha permissão para cortar",
      s1Body:
        "Use a sua própria live ou os VODs indicados pela campanha. Espere a transmissão acabar: o SupoClip trabalha com gravações, não com lives em andamento. Cole um link de VOD ou clipe da Twitch ou da Kick, um link do YouTube ou envie o arquivo. Os planos hospedados contam minutos de vídeo original, então um VOD de quatro horas consome 240 minutos, seja para aproveitar três cortes ou dez.",
      s2Heading: "2. Deixe o SupoClip montar a pré-seleção e procure o que ele não consegue ouvir",
      s2Body:
        "O SupoClip transcreve a live e propõe momentos com notas de gancho, engajamento, valor e potencial de compartilhamento. Use essas notas para decidir o que assistir primeiro, não como previsão de visualizações. Como a seleção parte da fala, ela manda muito bem em perseguições narradas, reações, opiniões e respostas ao chat, mas não percebe uma manobra silenciosa.",
      s3Heading: "3. Enquadre o streamer e a gameplay",
      s3BodyBefore:
        "A maioria das lives de GTA deixa a webcam em um canto da tela. Se você simplesmente cortar para 9:16, acaba com um rosto gigante ou só uma tirinha do jogo. O SupoClip detecta uma webcam fixa no canto e empilha o rosto em cima da gameplay:",
      s3BodyAfter:
        "Mesmo assim, assista ao corte inteiro em tamanho de celular. Confira se o minimapa ou o carro da perseguição continua no quadro e se as legendas não tampam a ação. Se o momento for pura gameplay, um enquadramento mais aberto funciona melhor do que um rosto enorme.",
      s4Heading: "4. Corte pensando em quem chegou agora",
      s4Body:
        "Quem aparece no feed não viu as últimas três horas da live. Comece já na situação, deixe só o contexto necessário e corte logo depois do desfecho. Abra o corte no estúdio para aparar ou dividir trechos, ou digite um comando no campo de edição com IA — como “corte o silêncio antes da perseguição” — e revise o resultado antes de salvar.",
      s5Heading: "5. Corrija as legendas",
      s5Body:
        "As legendas já vêm sincronizadas palavra por palavra, mas a transcrição automática nunca ouviu metade dos nomes de GTA 6. Corrija nomes de personagens e lugares (Lucia, Jason, Leonida, Vice City), nicknames e frases gritadas. Cada corte também ganha um título gancho curto no topo nos primeiros segundos; garanta que ele descreva o que realmente acontece, porque promessa quebrada faz perder retenção e reprovação na campanha.",
      editorAlt: "Editor de legendas do SupoClip com controles de estilo, transcrição palavra por palavra e prévia de área segura",
      editorCaption: "O editor do SupoClip com um vídeo de exemplo: transcrição, estilo de legenda e prévia lado a lado.",
      s6Heading: "6. Exporte, publique e envie o link",
      s6Body:
        "Exporte em 9:16 para TikTok, Shorts ou Reels, ou publique direto nas suas contas conectadas de TikTok e YouTube pelo próprio SupoClip. No plano Creator dá para agendar publicações com antecedência, o que ajuda a manter ritmo constante na semana de lançamento. Em campanhas, envie cada link assim que o vídeo entrar no ar e mantenha uma planilha simples: VOD original, timestamp, tipo de momento e visualizações.",
      scheduleLinkLabel: "Como montar e agendar uma semana de YouTube Shorts com uma gravação",
    },
    launchWeek: {
      heading: "Um plano para a semana de lançamento",
      lead: "Os primeiros dias depois do lançamento são os mais concorridos e os mais assistidos. Velocidade conta, mas um corte malfeito postado rápido ainda perde para um corte limpo postado uma hora depois. Um ritmo realista:",
    },
    mistakes: {
      heading: "Erros que custam visualizações para quem corta GTA 6",
      items: [
        {
          label: "Spoilers por alcance.",
          body: "Eles dão pico rápido, mas levam denúncia, caem e podem comprometer a conta inteira.",
        },
        {
          label: "Cortes que dependem da live inteira.",
          body: "“Ele finalmente conseguiu” não diz nada para quem está passando no feed. Deixe claro o que ele conseguiu.",
        },
        {
          label: "Terminar antes do desfecho.",
          body: "Se o vídeo pede para esperar alguma coisa acontecer, mostre o resultado.",
        },
        {
          label: "Enquadramento ilegível.",
          body: "HUD minúsculo e recorte que corta o carro da tela. Sempre teste em tamanho de celular.",
        },
        {
          label: "Música licenciada.",
          body: "Um hit da rádio no fundo do corte pode deixar o vídeo mudo ou transferir a receita para terceiros.",
        },
        {
          label: "Volume acima de qualidade.",
          body: "Vinte cortes quase iguais da mesma perseguição competem entre si. Poste o melhor.",
        },
      ],
      moreBeforeFirst: "Veja mais sobre como escolher momentos que funcionam sozinhos em ",
      moreFirstLink: "como encontrar os melhores momentos em um vídeo longo",
      moreBetween: ", e sobre lives em geral em ",
      moreSecondLink: "como transformar VODs da Twitch em cortes para TikTok e Shorts",
      moreAfter: ".",
    },
    sources: {
      heading: "Fontes",
      gtaSix: "Rockstar Games: Grand Theft Auto VI",
      rockstarPolicy: "Rockstar Games: Política sobre publicação de material protegido da Rockstar Games",
      whopContentRewards: "Whop: Content Rewards",
      youtubeMonetization: "Ajuda do YouTube: Políticas de monetização de canais",
      checkedNote:
        "Verificado em 17 de novembro de 2026. Valores de campanhas e regras das plataformas mudam; confira as versões atuais antes de contar com elas.",
    },
    calculator: {
      eyebrow: "Simulação interativa",
      title: "Quanto um mês de cortes de GTA 6 pode render",
      description:
        "Ajuste os controles para a sua realidade. O pagamento por corte é igual a visualizações aprovadas × valor da campanha, limitado ao teto por corte.",
      clipsPerWeekLabel: "Cortes postados por semana",
      clipsPerWeekHint: "Conte apenas cortes que você realmente teria orgulho de publicar.",
      viewsPerClipLabel: "Visualizações aprovadas por corte (média)",
      viewsPerClipHint: "As campanhas pagam pelas visualizações aprovadas na revisão dentro da janela de acompanhamento.",
      rateLabel: "Valor por 1.000 visualizações",
      rateHint: "Definido por cada campanha. Em torno de US$ 1 é uma referência comum.",
      capLabel: "Teto por corte",
      capNone: "Sem teto",
      capHint: "Muitas campanhas limitam o máximo que um único corte pode render. 0 significa sem teto.",
      vodHoursLabel: "Horas de live cortadas por mês",
      vodHoursUnit: "h",
      vodHoursHint: "O SupoClip hospedado contabiliza minutos de vídeo original, não quantidade de cortes.",
      payoutLabel: "Pagamento estimado",
      payoutDetail: (clipsPerMonth, formattedViews) => `${clipsPerMonth} cortes · ${formattedViews} views/mês`,
      perClipLabel: "Por corte",
      cappedDetail: "Cada corte bate no teto com essa média de visualizações.",
      uncappedDetail: "Abaixo do teto por corte.",
      afterPlanLabel: "Depois do plano SupoClip",
      planFitsDetail: (minutes, planName, formattedPrice, planMinutes) =>
        `${minutes} min de vídeo original cabem no ${planName} (${formattedPrice}/mês, ${planMinutes} min).`,
      planOverDetail: (minutes) =>
        `${minutes} minutos passam dos 1.500 do Creator. Corte menos horas ou faça auto-hospedagem.`,
      averageLead: "A média esconde a distribuição real.",
      averageBody:
        " Um corte com 200.000 visualizações e dezenove com 500 dão média de cerca de 10.500 views por corte, mas com teto de US$ 150 esse mês rende cerca de US$ 160, e não os US$ 210 que uma distribuição uniforme sugeriria. Espere que a maioria renda pouco e poucos carreguem o mês.",
      disclaimer:
        "Apenas uma estimativa, antes de impostos e taxas. Não é uma previsão de ganhos, e o orçamento da campanha pode acabar antes de todas as suas visualizações serem contabilizadas.",
    },
    workflowDiagram: {
      caption: "O fluxo de cortes de GTA 6 · você decide, o SupoClip faz a parte repetitiva",
      youLabel: "Você",
      appLabel: "SupoClip",
      steps: [
        { title: "Escolher o VOD", you: "Sua própria live ou um VOD liberado pela campanha", app: "Importa links da Twitch, Kick e YouTube ou arquivo local" },
        { title: "Pré-seleção", you: "Assiste aos candidatos e anota jogadas silenciosas", app: "Transcreve a fala e ranqueia momentos falados" },
        { title: "Enquadrar", you: "Confere cada troca de cena em tamanho de celular", app: "Empilha a webcam do canto acima da gameplay" },
        { title: "Cortar", you: "Gancho nos primeiros segundos e fim no desfecho", app: "Apara, divide e aplica edições com IA no estúdio" },
        { title: "Legendar", you: "Corrige nomes: Lucia, Jason, Leonida, Vice City", app: "Legendas sincronizadas por palavra e título gancho" },
        { title: "Publicar", you: "Envia os links para a campanha e registra os números", app: "Exporta em 9:16 e posta no TikTok e no YouTube" },
      ],
    },
    reactionLayout: {
      svgTitle: "De uma live 16:9 para um corte vertical 9:16 empilhado",
      svgDesc:
        "À esquerda, uma live em tela larga com a webcam no canto superior direito. À direita, o corte vertical: a webcam ocupa o terço superior e a gameplay preenche os dois terços inferiores.",
      sourceTitle: "VOD da live · 16:9",
      sourceSubtitle: "Webcam fixa em um canto superior",
      captionsHere: "legendas ficam aqui",
      outputTitle: "Corte · 9:16 · 1080×1920",
      figcaption:
        "Quando o SupoClip detecta um rosto estável em um canto superior da tela, coloca o streamer no terço de cima e a gameplay embaixo. Sem webcam fixa, ou com a câmera mudando de lugar? Ele recorre a outros layouts, então sempre revise o resultado.",
    },
    clipAnatomy: {
      eyebrow: "Anatomia de um corte",
      title: "Uma perseguição de 43 segundos em GTA 6 cortada para segurar a atenção",
      hookBarNote: "Título gancho na tela nos primeiros ~4 s",
      secondsUnit: "s",
      parts: [
        { label: "Gancho", from: 0, to: 3, note: "Diga ou mostre a situação: “seis estrelas com um minuto de jogo”." },
        { label: "Contexto", from: 3, to: 10, note: "Só o essencial para um desconhecido acompanhar." },
        { label: "Escalada", from: 10, to: 32, note: "A perseguição, o assalto ou a discussão com o chat." },
        { label: "Desfecho", from: 32, to: 40, note: "A batida, a fuga ou a reação." },
        { label: "Saída", from: 40, to: 43, note: "Pare aqui. Nada de “enfim, então…”" },
      ],
    },
    rulesBoard: {
      caption: "O que você pode postar · resumo informativo, não aconselhamento jurídico",
      groups: [
        {
          status: "ok",
          label: "Em geral ok",
          items: [
            "Sua própria gameplay com os seus comentários",
            "Momentos da história dentro de uma gameplay narrada",
            "Lives cujo dono roda uma campanha de cortes, seguindo as regras dela",
          ],
        },
        {
          status: "warn",
          label: "Confira antes",
          items: [
            "Monetização: a política da Rockstar Games foi escrita para uso não comercial",
            "Músicas licenciadas da rádio: podem silenciar ou reivindicar o vídeo",
            "Live de outra pessoa sem campanha nem autorização",
          ],
        },
        {
          status: "no",
          label: "Costuma cair",
          items: [
            "O final do jogo ou uma grande revelação da história",
            "Cutscenes isoladas e compilações cinematográficas",
            "Vazamentos, cheats e mods não autorizados",
          ],
        },
      ],
    },
    launchWeekTable: {
      caption: "Plano para a semana de lançamento de cortes de GTA 6 (lançamento: 19 de novembro de 2026)",
      headers: ["Quando", "Foco", "O que fazer"],
      rows: [
        [
          "Antes de 19/11",
          "Preparação",
          "Conecte TikTok e YouTube, escolha o estilo das legendas e leia as regras das campanhas em que quer entrar. Imagens antes do lançamento caem, então não poste nada.",
        ],
        [
          "19/11 (estreia)",
          "Fazer live ou assistir",
          "Jogue ou acompanhe as lives que você tem permissão para cortar. Anote os timestamps de jogadas silenciosas na hora em que acontecerem.",
        ],
        [
          "20/11",
          "Primeira leva",
          "Importe os VODs do dia anterior. Fique com os 3 a 5 momentos falados mais fortes e finalize direito em vez de postar vinte cortes crus.",
        ],
        [
          "21 a 23/11",
          "Ritmo constante",
          "Um VOD entra, alguns cortes saem, todo dia. Evite spoilers da história: cortes com spoiler levam denúncia e são removidos.",
        ],
        [
          "24 a 25/11",
          "Ler os números",
          "Compare que tipo de momento segurou mais público: perseguições, reações, descobertas no mapa ou opiniões. Faça mais cortes desse tipo.",
        ],
      ],
    },
    momentMap: {
      eyebrow: "Mapa de momentos · exemplo de live",
      title: "Uma live de quatro horas de GTA 6, três tipos de momento",
      description:
        "Clique em um marcador. O SupoClip lê a transcrição, então momentos falados entram na lista sugerida; jogadas silenciosas precisam do seu olhar, e spoilers ficam de fora.",
      legendAria: "Legenda",
      momentsAria: "Momentos da live",
      kinds: {
        spoken: { label: "Momento falado: a pré-seleção com IA encontra", short: "IA encontra" },
        silent: { label: "Ação silenciosa: revise manualmente", short: "Revisar à mão" },
        skip: { label: "Spoiler da história ou cutscene: deixe de fora", short: "Pular" },
      },
      moments: [
        { minute: 6, kind: "spoken", title: "Primeira reação à cena de abertura", note: "O streamer diz o que está vendo e por que ficou surpreso. A fala carrega o momento, então a transcrição encontra fácil." },
        { minute: 31, kind: "skip", title: "Cutscene da história", note: "Cutscenes isoladas estão entre os itens que a política de vídeos da Rockstar Games diz remover. Mantenha cenas da história dentro de comentários, ou deixe de fora." },
        { minute: 58, kind: "silent", title: "Quase batida na rodovia", note: "Uma jogada incrível sem nenhuma palavra por cima. Uma seleção guiada por fala pode ignorar; anote o timestamp enquanto assiste." },
        { minute: 84, kind: "spoken", title: "Chat pergunta sobre o tamanho do mapa", note: "Uma pergunta e uma resposta clara. Mantenha a pergunta na tela ou na primeira frase." },
        { minute: 112, kind: "spoken", title: "Perseguição cinco estrelas narrada", note: "Ação com narração ao vivo. Mantenha o começo, a perseguição e a reação no mesmo corte." },
        { minute: 139, kind: "silent", title: "Bug de física", note: "Engraçado de assistir, mas ninguém explica em voz alta. Encontre arrastando a linha do tempo e enquadre a ação, não o rosto." },
        { minute: 171, kind: "spoken", title: "Opinião polêmica sobre a trilha sonora", note: "Um ponto de vista com argumento. Silencie ou troque músicas licenciadas da rádio antes de postar." },
        { minute: 203, kind: "skip", title: "Revelação do final da história", note: "Postar o final ou uma grande reviravolta é exatamente o que a política mira, além de gerar denúncias." },
        { minute: 226, kind: "spoken", title: "Resumo: o melhor momento do dia", note: "O próprio streamer resume seu destaque. Ótimo para criar um título gancho e achar o momento exato no VOD." },
      ],
      showTableSummary: "Ver todos os momentos em tabela",
      tableHeaders: ["Tempo", "Momento", "Revisão"],
    },
  },
  creatorGuides: {
    "twitch-vod-to-shorts": {
      post: {
        title: "Como transformar VODs da Twitch em cortes para TikTok e Shorts",
        description:
          "Transforme um VOD da Twitch em cortes verticais: importe uma live encerrada, encontre destaques falados, revise o enquadramento da gameplay, adicione legendas e exporte para TikTok ou Shorts.",
        eyebrow: "Cortes de live",
        category: "Guia",
        readingTime: "Leitura de 6 min",
        summary:
          "Transforme um VOD da Twitch em cortes verticais: importe uma live encerrada, encontre destaques falados, revise o enquadramento da gameplay, adicione legendas e exporte para TikTok ou Shorts.",
        keywords: ["VOD da Twitch para Shorts", "VOD da Twitch para TikTok", "cortes da Twitch", "cortador de live com IA"],
      },
      intro:
        "Uma live longa mistura tipos bem diferentes de momentos: histórias, reações, explicações e jogadas que acontecem sem uma palavra. Cada um pede uma decisão de edição diferente. Use a pré-seleção com IA para os momentos falados e depois revise a gameplay e o enquadramento por conta própria antes de transformar um VOD da Twitch em cortes verticais.",
      sections: [
        {
          id: "import",
          heading: "1. Importe uma live já encerrada",
          paragraphs: [
            "Espere a live terminar e use o link de um VOD ou clipe da Twitch, ou envie a sua própria gravação. O SupoClip aceita essas fontes; ele não corta uma transmissão que ainda está ao vivo. Guarde uma cópia original das gravações que pretende reaproveitar.",
            "Se um VOD estiver indisponível, restrito ou não puder ser baixado, use uma gravação local que você tenha permissão para editar. Comece por uma gravação de tamanho gerenciável em vez de mandar todo o seu arquivo de uma vez. Confira os limites de envio e duração exibidos no aplicativo para o seu plano ou instalação.",
          ],
        },
        {
          id: "moments",
          heading: "2. Separe destaques falados de jogadas silenciosas",
          paragraphs: [
            "Envie a gravação para análise e revise os momentos sugeridos. A seleção guiada por transcrição funciona muito bem para uma história entre partidas, uma explicação de estratégia ou uma reação em que o comentário explica o que aconteceu.",
            "Uma jogada decisiva silenciosa ou um detalhe sutil na tela pode nunca aparecer na transcrição. Use timestamps das suas anotações para conferir esses trechos na gravação original. Não encare a falta de uma sugestão da IA como sinal de que nada interessante aconteceu.",
          ],
          table: {
            caption: "Qual tipo de revisão cada momento da live exige",
            headers: ["Momento", "O que procurar", "Revisar antes de exportar"],
            rows: [
              ["História ou opinião", "Uma abertura clara e um desfecho completo", "Remova referências ao chat anterior que quem chega não consegue ver"],
              ["Reação falada", "Tempo suficiente antes para entender o motivo da reação", "Mantenha o acontecimento e a reação no mesmo corte"],
              ["Gameplay silenciosa", "A jogada real na gravação original", "Selecione manualmente; a análise de fala pode deixar passar"],
              ["Resposta ao chat", "A pergunta somada a uma resposta clara", "Preserve o contexto sem expor informações privadas"],
            ],
          },
          links: [
            { href: "/blog/find-best-moments-in-video", label: "Confira se o destaque funciona sozinho" },
            { href: "/blog/how-to-clip-gta-6", label: "Fazendo cortes de lives de GTA 6? Siga o fluxo completo" },
          ],
        },
        {
          id: "frame",
          heading: "3. Mantenha a ação importante dentro do quadro vertical",
          paragraphs: [
            "Um recorte focado no rosto funciona bem quando você está conversando com a câmera. Mas pode ser a escolha errada quando quem assiste precisa ver o minimapa, o placar, o inventário ou uma visão aberta da jogada. Assista ao trecho inteiro no formato de destino e ajuste o enquadramento no editor.",
            "Se a ação não sobrevive a um recorte fechado, use um enquadramento que preserve mais da tela original em vez de forçar tudo em close. Este fluxo não promete um layout empilhado automático para qualquer combinação de webcam e jogo. Um plano mais aberto e legível serve melhor ao corte do que um rosto grande que esconde o que aconteceu.",
          ],
          bullets: [
            "Confira o início, o momento da jogada e toda troca de câmera ou cena.",
            "Garanta que as legendas não cubram a interface do jogo nem o foco da ação.",
            "Veja uma prévia no celular; textos pequenos do jogo podem ficar ilegíveis mesmo dentro do quadro.",
          ],
        },
        {
          id: "captions",
          heading: "4. Corrija nomes de jogos e apare o final",
          paragraphs: [
            "Legendas automáticas podem errar nicknames, nomes de personagens, siglas e palavras gritadas. Corrija isso na transcrição e ouça o áudio de novo. Mantenha gírias intencionais quando fazem parte do sentido, mas não deixe um erro de transcrição passar só porque parece plausível.",
            "Corte logo depois que a jogada ou a explicação se resolve. Se o corte termina com “espera até você ver o que acontece”, inclua aquilo que você pediu para a pessoa esperar. Um gancho honesto descreve a situação real em vez de fazer uma promessa que o vídeo não entrega.",
          ],
          links: [{ href: "/blog/word-by-word-video-captions", label: "Deixe as legendas legíveis sem esconder a ação" }],
        },
        {
          id: "export",
          heading: "5. Exporte para TikTok ou YouTube Shorts",
          paragraphs: [
            "Escolha a predefinição da plataforma e baixe o vídeo renderizado. Suba a versão do TikTok diretamente, ou publique usando os recursos habilitados na sua conta. No YouTube, você também pode publicar pelo SupoClip quando a integração estiver ativa; o agendamento depende do plano e do acesso configurado.",
            "Mantenha um registro simples com VOD de origem, timestamp, tema e plataforma publicada. Compare separadamente os resultados de histórias, reações e explicações. Isso mostra que tipo de momento da sua live o público valoriza em vez de tratar todos os cortes como o mesmo experimento.",
          ],
        },
      ],
      faqs: [
        {
          question: "O SupoClip consegue cortar minha live da Twitch enquanto estou ao vivo?",
          answer:
            "Não. Use um VOD ou clipe depois que a transmissão terminar, ou envie sua gravação. O fluxo descrito aqui é para vídeos gravados.",
        },
        {
          question: "A IA detecta todos os melhores momentos de gameplay?",
          answer:
            "Não. O SupoClip usa a transcrição para escolher momentos, então jogadas silenciosas importantes podem passar despercebidas. Revise eventos visuais manualmente e confira o enquadramento final.",
        },
        {
          question: "Este fluxo posta os cortes direto no TikTok?",
          answer:
            "Este guia usa exportações baixadas para o TikTok. Não presuma que a publicação direta no TikTok esteja ativa em toda conta; confira as plataformas conectadas e o acesso exibido nas suas configurações.",
        },
      ],
      cta: {
        title: "Dê uma segunda vida ao seu próximo VOD",
        body: "Importe uma live encerrada, revise os destaques falados e exporte os momentos que funcionam fora da transmissão ao vivo.",
        label: "Cortar meu VOD da Twitch",
      },
      gallery: {
        heading: "O que um corte guiado pela fala consegue demonstrar",
        intro:
          "Esta fonte é uma entrevista gravada, não uma live da Twitch nem uma demonstração de detecção de gameplay. Use o exemplo para avaliar o fluxo de trechos falados descrito acima; uma jogada silenciosa ou um layout de webcam sobre jogo exige revisão visual própria.",
        notes: {
          "ton-2":
            "Aqui são as palavras que sustentam a ideia, o que torna o trecho um bom exemplo de seleção por transcrição. Em uma live, aplique o mesmo teste de contexto a uma história ou explicação e depois confira separadamente se a ação do jogo continua visível.",
        },
      },
    },
    "podcast-to-shorts": {
      post: {
        title: "Como transformar um podcast em vídeo em Shorts e Reels",
        description:
          "Transforme um podcast em vídeo em Shorts e Reels legendados: escolha ideias completas, ajuste o enquadramento dos participantes, revise as legendas e exporte cortes com o SupoClip.",
        eyebrow: "Fluxo para criadores",
        category: "Guia",
        readingTime: "Leitura de 7 min",
        summary:
          "Transforme um podcast em vídeo em Shorts e Reels legendados: escolha ideias completas, ajuste o enquadramento dos participantes, revise as legendas e exporte cortes com o SupoClip.",
        keywords: ["podcast para Shorts", "cortes de podcast", "podcast para Reels", "cortador de podcast em vídeo"],
        imageAlt: "Ton Roosendaal na entrevista original usada no exemplo de cortes de conversa do SupoClip",
      },
      intro:
        "Um bom corte de podcast entrega uma ideia completa para quem nunca viu o canal, sem exigir que a pessoa assista ao episódio inteiro antes. Comece com uma pergunta, uma observação surpreendente ou uma lição específica e mantenha a explicação que sustenta o desfecho. Veja um processo repetível para fazer isso com um podcast em vídeo, da gravação original até a exportação vertical.",
      sections: [
        {
          id: "prepare",
          heading: "1. Comece com uma gravação que possa ser reaproveitada",
          paragraphs: [
            "Use a versão em vídeo do episódio: um link do YouTube ou um arquivo de vídeo para enviar. Uma faixa de voz limpa vale mais do que um cenário elaborado. Se o áudio estiver abafado, corrija antes de pedir a um sistema de transcrição que encontre bons trechos.",
            "Escolha um episódio com respostas ou histórias bem definidas. Uma conversa sobre um erro concreto, uma decisão ou um resultado vira corte independente com muito mais facilidade do que uma introdução longa. Confirme que você tem permissão para reutilizar a gravação e a participação dos convidados. Este fluxo parte de vídeo gravado; um episódio só em áudio precisa primeiro de um tratamento visual.",
          ],
          bullets: [
            "Guarde o título do episódio, o nome do convidado e o link original nas anotações do projeto.",
            "Confira a duração do vídeo original frente ao limite do seu plano hospedado antes de enviar um episódio longo.",
            "Se não for possível importar por link, envie o arquivo de vídeo original diretamente.",
          ],
        },
        {
          id: "select",
          heading: "2. Gere sugestões e escolha ideias completas",
          paragraphs: [
            "Cole o link do episódio no SupoClip ou envie o arquivo. O app transcreve a fala e sugere trechos curtos. Encare as pontuações como ordem de revisão: uma nota alta mostra qual candidato olhar primeiro, não quantas visualizações ele vai ter.",
            "Assista a cada sugestão isolada do resto da conversa. Dá para entender o assunto logo na primeira frase? A resposta entrega algo concreto? Um corte que começa com “esse era o problema” precisa de uma frase anterior ou de outro ponto de entrada. Tire saudações e rodeios, mas mantenha qualquer ressalva que mude o sentido do que foi dito.",
          ],
          links: [{ href: "/blog/find-best-moments-in-video", label: "Use o checklist para escolher melhores momentos" }],
        },
        {
          id: "example",
          heading: "Exemplo prático: cortando uma entrevista gravada",
          paragraphs: [
            "Uma entrevista gravada tem o mesmo desafio de contexto de um podcast em vídeo: uma resposta pode depender da pergunta ou de algo dito minutos antes. Nos exemplos abaixo, rodamos no SupoClip uma entrevista em Creative Commons com Ton Roosendaal, criador do Blender, e renderizamos trechos completos com legendas.",
            "Use os links da fonte para comparar cada corte com o original. Ouça se o assunto está claro, acompanhe a explicação e veja o que ficou de fora. São exemplos do fluxo de cortes, não uma afirmação de que o entrevistado usa ou recomenda o SupoClip.",
          ],
        },
        {
          id: "edit",
          heading: "3. Revise o enquadramento dos falantes e as legendas juntos",
          paragraphs: [
            "Abra o corte escolhido no editor. Use o enquadramento vertical para Shorts e Reels e assista ao trecho do começo ao fim. Um recorte que funciona bem no apresentador pode cortar o convidado quando a câmera muda. Quando um gráfico ou um plano aberto com duas pessoas importa, preserve área suficiente do quadro original para que faça sentido.",
            "Corrija nomes, marcas, números e pontuação na transcrição. Mantenha as legendas longe dos rostos e dos botões da plataforma. Confira a prévia em tamanho de celular em vez de julgar a leitura na tela grande do monitor. Se o estilo da legenda roubar atenção da resposta, simplifique a animação e destaque menos palavras.",
          ],
          links: [{ href: "/blog/word-by-word-video-captions", label: "Estilize e corrija legendas palavra por palavra" }],
        },
        {
          id: "publish",
          heading: "4. Exporte cada corte com um motivo claro para assistir",
          paragraphs: [
            "Exporte usando a predefinição do destino. Dê a cada corte um título que reflita a lição real do vídeo. Evite um gancho exagerado que o convidado nunca sustenta: quem clica precisa encontrar a resposta prometida dentro do próprio corte.",
            "Publique o arquivo exportado no Reels ou envie ao YouTube pelo SupoClip quando a publicação estiver habilitada na sua conta. O agendamento depende do plano e do acesso configurado na instalação. Deixe o link do episódio completo na descrição ou no perfil para quem quiser assistir à conversa inteira.",
          ],
          bullets: [
            "Assista ao arquivo exportado do começo ao fim, incluindo o primeiro e o último segundo.",
            "Verifique se nenhum texto importante fica coberto pelos botões do aplicativo de destino.",
            "Anote de qual episódio e de qual momento original saiu cada corte.",
            "Analise retenção e respostas reais do público antes de decidir quais temas cortar na próxima leva.",
          ],
          links: [{ href: "/blog/schedule-week-of-youtube-shorts", label: "Monte uma semana de Shorts a partir de uma gravação" }],
        },
      ],
      faqs: [
        {
          question: "Posso fazer Reels a partir de um podcast no YouTube?",
          answer:
            "Sim. Se você tem permissão para reutilizar o vídeo, use o link do YouTube como fonte, crie o corte vertical e baixe a exportação para Reels. Depois envie esse arquivo para o Instagram; este guia não parte do princípio de que a publicação direta no Instagram esteja disponível.",
        },
        {
          question: "Quantos cortes devo tirar de um episódio?",
          answer:
            "Fique com tantos quantos tiverem ideias distintas e completas que mereçam ir ao ar. Não existe um número fixo por episódio, e repetir a mesma resposta com outro gancho não cria um novo conteúdo.",
        },
        {
          question: "Um corte curto pode omitir a pergunta do entrevistador?",
          answer:
            "Sim, quando a resposta já deixa claro o assunto e funciona sozinha. Caso contrário, mantenha uma versão curta da pergunta ou comece um pouco antes para preservar o contexto.",
        },
      ],
      cta: {
        title: "Transforme seu próximo episódio de podcast em cortes",
        body: "Traga um episódio em vídeo. Revise os momentos sugeridos, corrija as legendas e exporte os cortes que você realmente publicaria. O SupoClip hospedado é pago; a auto-hospedagem é outra opção.",
        label: "Cortar meu podcast",
      },
      gallery: {
        heading: "De uma conversa gravada a uma resposta completa",
        intro:
          "Estes exemplos usam uma entrevista com Ton Roosendaal, fundador do Blender. É uma entrevista e não um episódio de podcast, mas o teste editorial é o mesmo: a resposta apresenta o tema e entrega uma ideia útil sem precisar do resto da conversa?",
        notes: {
          "ton-1":
            "Preste atenção ao assunto já na primeira frase e acompanhe a explicação até o fim. Compare o corte com a fonte para conferir se o sentido do que foi dito sobrevive ao formato curto.",
          "ton-2":
            "Avalie a segunda resposta como uma publicação independente, não como continuação. As legendas e o enquadramento precisam ajudar quem nunca viu o primeiro corte a entender tudo.",
        },
      },
    },
    "find-best-moments-in-video": {
      post: {
        title: "Como encontrar os melhores momentos em um vídeo longo",
        description:
          "Escolha cortes mais fortes com um checklist prático de gancho, contexto, desfecho e pontos de corte. Use sugestões de IA sem depender de uma nota de viralidade.",
        eyebrow: "Fluxo para criadores",
        category: "Guia",
        readingTime: "Leitura de 6 min",
        summary:
          "Escolha cortes mais fortes com um checklist prático de gancho, contexto, desfecho e pontos de corte. Use sugestões de IA sem depender de uma nota de viralidade.",
        keywords: ["encontrar melhores momentos em vídeo", "escolher cortes de vídeo", "detecção de destaques com IA", "cortes de podcast"],
      },
      intro:
        "O melhor momento de uma gravação nem sempre é o mais alto, engraçado ou emotivo. Para funcionar como vídeo curto, ele também precisa se sustentar fora do contexto original. Use as verificações abaixo para transformar um vídeo longo em uma lista enxuta de momentos que qualquer pessoa nova no seu perfil consiga entender.",
      sections: [
        {
          id: "brief",
          heading: "Defina quem o corte precisa ajudar",
          paragraphs: [
            "Escreva uma frase antes de começar a procurar: por exemplo, “Este corte deve ajudar um fundador de primeira viagem a conduzir uma conversa difícil com um cliente”. Um público específico dá um critério real de seleção. Sem isso, qualquer frase de efeito parece um bom corte.",
            "Passe pelos capítulos, perguntas e mudanças de assunto buscando respostas para esse objetivo. Bons candidatos costumam trazer um erro concreto, uma explicação de antes e depois, uma demonstração prática ou uma discordância bem fundamentada. Marque timestamps aproximados; você vai refinar os cortes depois.",
          ],
        },
        {
          id: "checklist",
          heading: "Passe por quatro checagens antes de aprovar um momento",
          paragraphs: [
            "Assista ao trecho como se ele tivesse aparecido no feed sem o título do episódio. Cada checagem responde a uma pergunta diferente. Uma abertura chamativa não compensa uma explicação pela metade, e uma boa explicação ainda pode precisar de uma primeira frase mais limpa.",
          ],
          table: {
            caption: "Checklist editorial para um corte independente",
            headers: ["Checagem", "Mantenha quando", "Revise quando"],
            rows: [
              ["Gancho", "A abertura nomeia um problema, uma tensão ou um resultado específico", "Começa com cumprimentos ou um “isso” e “aquilo” sem explicação"],
              ["Contexto", "O assunto e as condições relevantes estão claros", "Você precisa dos cinco minutos anteriores para entender"],
              ["Desfecho", "Quem fala entrega um motivo, um exemplo ou uma resposta útil", "O corte faz uma promessa, mas termina antes da resposta"],
              ["Limites", "O corte começa e termina em pensamentos completos", "Um corte muda o sentido original ou interrompe a conclusão"],
            ],
          },
          bullets: [
            "Ouça uma vez sem olhar para o vídeo: o argumento continua coerente?",
            "Assista uma vez sem som: o enquadramento e as legendas sustentam o tema?",
            "Confira a gravação original antes e depois dos cortes para ver se não removeu uma ressalva importante.",
          ],
        },
        {
          id: "example",
          heading: "Compare uma ideia completa com uma frase isolada",
          paragraphs: [
            "Uma frase genérica sobre praticar uma habilidade pode parecer um bom recorte, mas deixa de fora a explicação útil. Um corte mais forte inclui a ação recomendada por quem fala e o porquê dela. A gravação original ao redor dos dois limites é a referência para saber se a versão curta continua fiel.",
            "Os exemplos de Andrew Price abaixo vêm da mesma apresentação conhecida. Aplique o checklist a cada um: o trecho mantém a explicação que torna a abertura útil? Se um candidato precisa de mais contexto do que um vídeo curto comporta, deixe esse trecho no vídeo completo.",
          ],
        },
        {
          id: "ai",
          heading: "Deixe a IA reduzir a busca e tome a decisão final",
          paragraphs: [
            "Um cortador de vídeo com IA pode transcrever a gravação, sugerir segmentos e ordená-los para revisão. No SupoClip, as notas de gancho, engajamento, valor e compartilhamento ajudam a organizar essa pré-seleção. Elas são sinais editoriais, não previsão de visualizações.",
            "A análise baseada em transcrição funciona melhor quando o momento interessante está na fala. Uma demonstração silenciosa, uma revelação visual ou uma jogada decisiva podem ser excelentes mesmo sem narração. Revise esses momentos manualmente em vez de presumir que a busca por texto achou tudo.",
          ],
          links: [{ href: "/ai-video-clipper", label: "Veja o que o cortador de vídeo com IA automatiza" }],
        },
        {
          id: "refine",
          heading: "Refine o corte antes de colocar efeitos",
          paragraphs: [
            "Posicione o início logo antes do primeiro pensamento completo que importa. Remova repetições na introdução sempre que possível e ouça o final junto com a frase anterior. Encerre logo após o desfecho, sem arrastar o assunto seguinte para dentro do corte. Preserve pausas que ajudam a entender a resposta.",
            "Só depois ajuste legendas, enquadramento e título gancho. Anote em uma linha por que você escolheu aquele momento. Depois de publicar, compare a reação do público com essa hipótese. Se as pessoas saem antes da explicação, teste uma abertura mais direta; se assistem mas não interagem, reavalie se a lição realmente interessa ao público que você escolheu.",
          ],
          links: [
            { href: "/blog/podcast-to-shorts", label: "Aplique o checklist em um podcast em vídeo" },
            { href: "/blog/repurpose-webinar-clips", label: "Aplique em uma gravação de webinar" },
          ],
        },
      ],
      faqs: [
        {
          question: "Uma nota alta de viralidade garante que o corte vai bombar?",
          answer:
            "Não. As notas ajudam a priorizar o que revisar primeiro, mas distribuição, encaixe com o público, edição final e a própria gravação continuam decisivos. Avalie o desempenho pelos resultados reais após publicar.",
        },
        {
          question: "Todo corte precisa ter a mesma duração?",
          answer:
            "Não. Fique com o corte mais enxuto que entregue uma ideia completa e fiel. Confira separadamente os requisitos atuais de formato de cada plataforma.",
        },
        {
          question: "E se o melhor momento for quase todo visual?",
          answer:
            "Inspecione manualmente. A seleção guiada por transcrição pode deixar passar eventos que não são descritos em voz alta. Escolha um enquadramento que preserve a ação visual em vez de presumir que um recorte no rosto resolve.",
        },
      ],
      cta: {
        title: "Monte uma pré-seleção da sua própria gravação",
        body: "Use o SupoClip para sugerir momentos e depois aplique as checagens de gancho, contexto, desfecho e limites antes de exportar.",
        label: "Encontrar meus melhores momentos",
      },
      gallery: {
        heading: "Duas ideias diferentes da palestra popular de Andrew Price",
        intro:
          "A palestra original tinha 3,8 milhões de visualizações no YouTube quando selecionada em 26 de outubro de 2026. Estes são cortes recém-renderizados no SupoClip, não capturas de Shorts de terceiros. Assista aos exemplos completos e use os links da fonte para conferir os limites de cada corte.",
        notes: {
          "price-1":
            "Aplique as quatro checagens: identifique a ideia de abertura, o contexto que a mantém fiel, a explicação que entrega o desfecho e o ponto exato em que o trecho termina.",
          "price-2":
            "Compare esta ideia com o primeiro corte. Manter lições distintas vale mais do que bater meta com várias versões da mesma resposta.",
        },
      },
    },
    "word-by-word-video-captions": {
      post: {
        title: "Como colocar legendas palavra por palavra em vídeos curtos",
        description:
          "Crie legendas palavra por palavra fáceis de ler para Shorts, Reels e TikTok. Corrija a transcrição, escolha o estilo, confira as áreas seguras e exporte com o SupoClip.",
        eyebrow: "Fluxo para criadores",
        category: "Guia",
        readingTime: "Leitura de 6 min",
        summary:
          "Crie legendas palavra por palavra fáceis de ler para Shorts, Reels e TikTok. Corrija a transcrição, escolha o estilo, confira as áreas seguras e exporte com o SupoClip.",
        keywords: ["legendas palavra por palavra", "legendas animadas", "colocar legendas em Shorts", "legendas automáticas em vídeo"],
        imageAlt: "Editor do SupoClip com estilos de legenda, tempo por palavra e prévia de vídeo vertical",
      },
      intro:
        "Legendas palavra por palavra destacam a fala no exato momento em que ela acontece. Mas só ajudam quando as palavras estão corretas, fáceis de ler e posicionadas onde quem assiste consegue enxergar. Construa o estilo a partir da sua gravação em vez de escolher a animação mais chamativa logo de cara.",
      sections: [
        {
          id: "transcribe",
          heading: "1. Gere o corte e revise a transcrição",
          paragraphs: [
            "Importe um link suportado ou envie um vídeo para o SupoClip. Assim que o corte for gerado, abra-o no editor e acompanhe a transcrição junto com a reprodução. As marcações de tempo por palavra ligam o texto ao áudio falado.",
            "Comece pela precisão. Confira nomes, números, termos técnicos e trechos falados por cima de música ou de outra voz. Ouça qualquer trecho duvidoso dentro da frase inteira antes de alterar. As legendas devem refletir o que foi realmente dito, sem transformar uma fala incerta em uma afirmação que a pessoa não fez.",
          ],
        },
        {
          id: "correct",
          heading: "2. Corrija as palavras antes de escolher o estilo",
          paragraphs: [
            "Use os controles da transcrição para corrigir uma palavra mal transcrita e ouça aquele trecho de novo. Uma correção ortográfica não precisa mexer em um tempo que já está certo. Se você reescrever um trecho maior, confira a sincronização resultante em vez de presumir que o novo texto encaixa no ritmo antigo.",
            "Limpe ruídos de transcrição que distraem, mas mantenha ressalvas importantes para o sentido. Por exemplo, “quinze por cento” e “cinquenta por cento” podem parecer igualmente plausíveis no texto; quem manda no corte final é o áudio.",
          ],
          example: "editor",
        },
        {
          id: "style",
          heading: "3. Escolha um estilo de legenda fácil de ler",
          paragraphs: [
            "Comece por uma predefinição de legenda e depois ajuste fonte, tamanho, cores, posição e destaque da palavra ativa. Use uma paleta enxuta e consistente. Destacar todas as palavras com uma troca agressiva de cor pode competir com uma demonstração ou com uma cena agitada.",
            "Teste no pior quadro do vídeo, não apenas em um frame limpo. Texto branco que fica ótimo sobre uma camisa escura pode sumir quando a câmera corta para um slide claro. Um fundo contrastante ou contorno ajuda, desde que não esconda o assunto. Só salve um estilo depois de assisti-lo em um corte representativo inteiro.",
          ],
          bullets: [
            "Escolha uma fonte que continue legível em tela de celular.",
            "Quebre as legendas em blocos curtos e fáceis de acompanhar em vez de ocupar a largura toda.",
            "Mantenha o texto longe de rostos, gráficos e botões na tela.",
            "Aplique o estilo salvo com consistência, mas revise exceções em cada corte.",
          ],
        },
        {
          id: "safe-area",
          heading: "4. Confira as áreas seguras de cada plataforma",
          paragraphs: [
            "Os aplicativos de vídeo curto colocam botões e descrições ao redor e por cima da imagem. Use a prévia de área segura do editor como guia e depois confira o vídeo exportado no aplicativo de destino antes de publicar. Não existe uma única posição de legenda que funcione para todo app, aparelho e gravação.",
            "Preste atenção especial à área inferior da descrição e aos ícones na lateral. Se o vídeo original já traz texto na tela, mova ou simplifique as legendas para que duas mensagens não se sobreponham. A melhor posição também pode mudar quando você sai de um recorte no rosto para uma gravação de tela mais aberta.",
          ],
          table: {
            caption: "Problemas de legenda para conferir na revisão",
            headers: ["Problema", "O que observar", "Ajuste"],
            rows: [
              ["Palavras somem", "Trocas entre cenas claras e escuras", "Aumente o contraste ou use um fundo legível"],
              ["Legenda parece atrasada", "A palavra falada comparada ao destaque", "Revise o tempo na transcrição e a renderização final"],
              ["Texto esconde a ação", "Rostos, mãos, slides e interface do jogo", "Reposicione a legenda ou reduza a quantidade de palavras por vez"],
              ["Movimento demais", "Animação durante falas rápidas", "Escolha um estilo mais limpo e blocos de texto menores"],
            ],
          },
        },
        {
          id: "export",
          heading: "5. Exporte e assista sem som",
          paragraphs: [
            "Escolha a predefinição da plataforma e renderize o corte. As legendas renderizadas pelo SupoClip ficam gravadas na imagem do vídeo, então permanecem visíveis quando você sobe o arquivo. Assista ao arquivo exportado com som para checar o sincronismo e depois sem som para confirmar se o assunto e o raciocínio continuam claros.",
            "Legendas gravadas no vídeo não podem ser desativadas por quem assiste como uma faixa separada de legendas ocultas. Elas também não substituem todos os recursos de acessibilidade que uma plataforma oferece. Se você ativar legendas extras na própria plataforma, confira se os dois textos não se duplicam de forma confusa.",
          ],
          links: [
            { href: "/youtube-shorts-clipper", label: "Finalize e exporte um YouTube Short" },
            { href: "/blog/podcast-to-shorts", label: "Use legendas no fluxo de cortes de podcast" },
          ],
        },
      ],
      faqs: [
        {
          question: "Posso corrigir legendas geradas automaticamente?",
          answer:
            "Sim. Revise a transcrição no editor de cortes, corrija palavras mal reconhecidas e confira a reprodução antes de exportar. Verifique novamente o sincronismo após alterações maiores no texto.",
        },
        {
          question: "Preciso de um estilo diferente para cada plataforma?",
          answer:
            "Você pode reutilizar o mesmo estilo visual, mas confira posição e leitura em cada destino. Os controles de cada aplicativo e o próprio conteúdo do vídeo podem pedir ajustes de posicionamento.",
        },
        {
          question: "Quem assiste pode desativar essas legendas?",
          answer:
            "Legendas renderizadas na imagem do vídeo ficam sempre visíveis. Elas são diferentes de uma faixa de legenda separada que a plataforma permite ativar ou desativar.",
        },
      ],
      cta: {
        title: "Deixe seu próximo corte mais fácil de acompanhar",
        body: "Gere um corte, corrija a transcrição e crie um estilo de legenda que continue nítido no celular.",
        label: "Criar um corte legendado",
      },
      gallery: {
        heading: "Leia as legendas prontas enquanto o falante conversa",
        intro:
          "Estes cortes completos foram transcritos e renderizados no SupoClip. Assista com som para conferir o sincronismo e depois tire o áudio para avaliar a leitura sobre fundos que mudam.",
        notes: {
          "price-1":
            "Acompanhe o destaque da palavra ativa ao longo de uma frase inteira. Julgue o texto na largura exibida de celular em vez de ampliar o player para fazer uma fonte pequena parecer legível.",
          "ton-1":
            "Use um segundo falante e outra gravação para testar o mesmo estilo de legenda. Reaproveitar uma predefinição ainda pede conferir nomes, contraste, quebras de linha e posição em cada corte.",
        },
      },
      editorFigure: {
        ariaLabel: "Abrir a captura em tamanho real do editor do SupoClip",
        alt: "Editor de legendas do SupoClip mostrando controles de estilo, transcrição por palavra e prévia de área segura",
        caption:
          "Captura do editor usando um vídeo de exemplo. Os controles de legenda e a transcrição ficam ao lado da prévia; abra a imagem para ver em tamanho real.",
      },
    },
  },
};
