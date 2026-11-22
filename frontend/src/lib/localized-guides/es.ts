import type { TranslatedGuideBundle } from "./types";

export const esGuideBundle: TranslatedGuideBundle = {
  locale: "es",
  shell: {
    seeHostedPlans: "Ver planes alojados",
    faqTocLabel: "Preguntas frecuentes",
  },
  clipExamplesChrome: {
    eyebrow: "Hecho con SupoClip",
    fullClipMeta: (durationSeconds, sourceRange) => `Clip completo de ${durationSeconds}s · origen ${sourceRange}`,
    videoAriaLabel: (clipTitle, sourceTitle) => `${clipTitle} — ejemplo de SupoClip tomado de ${sourceTitle}`,
    sourcePrefix: "Fuente:",
    watchOriginal: "Ver el momento original",
    detailsSummary: "Fuente, licencia y ediciones",
    viewsNote: (formattedViews, checkedAt) => `${formattedViews} visualizaciones en YouTube al seleccionarlo el ${checkedAt}.`,
    licenseRecord: "Registro de fuente y licencia",
    noEndorsement: "Estos ejemplos editoriales no implican respaldo por parte de los hablantes ni del canal original.",
    downloadAttribution: "Descargar atribución y notas de procesamiento",
    footerNote:
      "Estos son clips de ejemplo completos, codificados en 540×960 para esta página. Los tiempos de origen corresponden a la grabación original; las pausas eliminadas pueden hacer que el clip final dure menos que ese tramo.",
  },
  gta6: {
    post: {
      title: "Cómo hacer clips de GTA 6 (y cuánto paga realmente)",
      description:
        "Convierte directos y streams de GTA 6 en TikToks y Shorts: cuánto pagan las campañas de clipping, las reglas de Rockstar Games y el flujo paso a paso del VOD a clips verticales con subtítulos.",
      eyebrow: "Clips de GTA 6",
      category: "Guía",
      readingTime: "Lectura de 10 min",
      summary:
        "Todo el mundo está haciendo clips de GTA 6. Aquí tienes cuánto paga de verdad, qué clips bajan por reglas o spoilers y el flujo para pasar de un stream de cuatro horas a clips verticales que valgan la pena.",
      keywords: [
        "cómo hacer clips de GTA 6",
        "clips de GTA 6",
        "ganar dinero haciendo clips de GTA 6",
        "campañas de clipping GTA 6",
        "clips de GTA 6 para TikTok",
        "recortar streams de GTA 6",
      ],
      imageAlt: "Ilustración de un stream al atardecer al estilo GTA con webcam en la esquina convirtiéndose en tres clips verticales con subtítulos",
    },
    breadcrumb: "Cómo hacer clips de GTA 6",
    toc: {
      whyNow: "Por qué GTA 6",
      whatItPays: "Cuánto paga",
      rules: "Las reglas",
      workflow: "El flujo",
      launchWeek: "Semana de estreno",
      mistakes: "Errores comunes",
      faq: "Preguntas frecuentes",
    },
    faqs: [
      {
        question: "¿De verdad se puede ganar dinero haciendo clips de GTA 6?",
        answer:
          "Algunos clippers sí ganan, sobre todo con campañas que pagan por cada 1.000 vistas aprobadas, a menudo cerca de US$ 1. La mayoría de los clips genera muy poco y unos pocos sostienen el mes, los presupuestos de campaña se agotan y muchas ponen un tope por clip. Tómalo como práctica pagada de edición, no como un ingreso asegurado.",
      },
      {
        question: "¿Es legal subir clips de GTA 6?",
        answer:
          "La política de videos de Rockstar Games permite videos de fans para uso no comercial y le da a Take-Two el derecho de retirar cualquier material en cualquier momento. Apunta de forma específica al final de la historia, grandes revelaciones, cinemáticas aisladas y material previo al lanzamiento. La monetización no se concede de forma explícita, así que lee la política y las reglas de tu campaña antes de contar con ese dinero.",
      },
      {
        question: "¿Puede SupoClip recortar un stream de GTA 6 mientras está en vivo?",
        answer:
          "No. SupoClip trabaja sobre video grabado: un VOD o clip de Twitch o Kick, un video de YouTube o un archivo que subas tú. Recorta el VOD en cuanto termine el directo.",
      },
      {
        question: "¿La IA va a encontrar todos los buenos momentos de GTA 6?",
        answer:
          "No. SupoClip elige momentos a partir de lo que se dice, así que persecuciones narradas, reacciones y opiniones aparecen en su preselección. Un choque evitado en silencio o un bug de físicas puede pasar desapercibido. Anota esas marcas de tiempo mientras miras y córtalas a mano.",
      },
      {
        question: "¿Cuántos clips de GTA 6 salen de un solo stream?",
        answer:
          "La preselección de SupoClip crece con la duración del video, alrededor de un candidato cada cinco minutos hasta un tope. Quédate solo con los que funcionan por sí solos; unos pocos clips bien terminados rinden más que veinte a medias.",
      },
      {
        question: "¿Qué pasa con la música de la radio del juego?",
        answer:
          "Las emisoras de radio de GTA reproducen canciones con licencia. Pueden hacer que silencien o reclamen tu clip en TikTok y YouTube, aparte de la política de Rockstar Games. Corta evitando esas canciones o baja la música siempre que puedas.",
      },
    ],
    cta: {
      title: "Haz clips de tu primer stream de GTA 6",
      body: "Pega un VOD terminado de Twitch, Kick o YouTube. Revisa la preselección, ajusta el encuadre y los subtítulos, y publica los clips que tú mismo verías completos.",
      label: "Recortar un VOD de GTA 6",
    },
    heroCaption: "Ilustración original. SupoClip no está afiliado a Rockstar Games ni a Take-Two Interactive.",
    intro: [
      "Tu feed está lleno de ellos: la cara del streamer arriba, una persecución de GTA abajo, subtítulos saltando palabra por palabra y alguien en los comentarios preguntando cuánto le pagaron al clipper. Hacer clips de GTA 6 es la jugada obvia de la temporada, y sí le paga a algunas personas: le paga a quienes lo tratan como edición de verdad y no como un boleto de lotería.",
      "Esta guía cubre el flujo completo: de dónde salen los clips, cuánto pagan realmente las campañas, qué reglas hacen que bajen un video y cómo pasar de un VOD de cuatro horas a un puñado de clips verticales con SupoClip. Las herramientas visuales de la página son interactivas; ajusta los números a tu caso.",
    ],
    whyNow: {
      heading: "Por qué todo el mundo está haciendo clips de GTA 6 ahora mismo",
      p1LinkText: "Grand Theft Auto VI",
      p1AfterLinkBeforeDate: " sale el ",
      p1Date: "19 de noviembre de 2026",
      p1AfterDate:
        " en PlayStation 5 y Xbox Series X|S, con versión para PC prevista para después. Transcurre en el estado de Leonida, alrededor de Vice City, y sigue a Lucia y Jason. La semana de lanzamiento apunta a ser una de las más transmitidas del año, y la mayoría de la gente la verá en clips cortos antes que en VODs de cuatro horas.",
      p2BeforeStrong: "Esa es la oportunidad en una sola frase: ",
      p2Strong: "los streams son largos y los feeds piden de 30 a 60 segundos",
      p2AfterStrong:
        ". Alguien tiene que encontrar el momento, cortarlo para que un desconocido lo entienda al instante, encuadrarlo para el teléfono y subtitularlo. Streamers, marcas y agencias pagan a clippers por hacerlo, y quienes juegan en directo lo hacen para hacer crecer sus propios canales.",
    },
    whatItPays: {
      heading: "Cuánto paga realmente hacer clips de GTA 6",
      lead: "Hay dos maneras de convertir clips de GTA 6 en dinero, y funcionan de formas muy distintas.",
      li1Label: "Campañas de clipping.",
      li1BeforeLink: " Un streamer o una marca fondea un presupuesto en un mercado como ",
      li1LinkText: "Whop Content Rewards",
      li1AfterLink:
        ", fija una tarifa por cada 1.000 vistas y establece las reglas del material. Publicas clips en tus propias cuentas, envías los enlaces y cobras por las vistas que pasan la revisión. Whop sitúa el promedio alrededor de US$ 1 por cada 1.000 vistas; cada campaña de GTA define su propia tarifa, a menudo un tope por clip, y deja de pagar en cuanto se agota el presupuesto.",
      li2Label: "Tu propio canal.",
      li2BeforeLink:
        " Si tú mismo juegas y comentas, los clips llevan público a tus directos y pueden generar ingresos en los programas de cada plataforma. Las ",
      li2LinkText: "políticas de monetización de YouTube",
      li2AfterLink:
        " excluyen contenido reutilizado sin comentario ni edición sustancial, así que resubir gameplay ajeno sin aportar nada es el plan equivocado.",
      mathIntro:
        "La cuenta en campañas es simple y realista. A US$ 1 por cada 1.000 vistas, 100.000 vistas aprobadas son US$ 100. Prueba tus propios números abajo, incluyendo lo que cuesta el plan de SupoClip que necesitarías:",
    },
    rules: {
      heading: "Las reglas por las que bajan clips de GTA 6",
      p1BeforeLink: "Rockstar Games publica una ",
      p1LinkText: "política sobre la publicación de su material protegido por derechos de autor",
      p1BetweenLinkAndEm: ". Permite videos de fans para uso ",
      p1Em: "no comercial",
      p1AfterEm:
        ", que define como no ganar dinero con las imágenes, y se reserva el derecho de retirar cualquier video. Señala en concreto videos del final o grandes revelaciones de la historia, recopilaciones editadas de cinemáticas y material previo al estreno. Las cinemáticas dentro de una partida narrada más larga suelen aceptarse.",
      p2: "Además, cada campaña tiene sus propias reglas: qué streams puedes usar, etiquetas o enlaces obligatorios, duración mínima del clip y prohibición de trucos como comprar vistas. Si las rompes, las vistas no cuentan. Léelas antes de editar, no después.",
    },
    workflow: {
      heading: "El flujo: de un VOD de GTA 6 a clips listos para publicar",
      lead: "Aquí está todo el proceso de un vistazo. La división importa: SupoClip hace la parte repetitiva (transcribir, preseleccionar, reencuadrar y sincronizar subtítulos) para que tú dediques la atención a las decisiones que solo un editor puede tomar.",
      s1Heading: "1. Elige un VOD que tengas permiso para recortar",
      s1Body:
        "Usa tu propio stream o los VODs que indique la campaña. Espera a que termine la transmisión: SupoClip trabaja con grabaciones, no con directos en curso. Pega un enlace de VOD o clip de Twitch o Kick, un enlace de YouTube o sube el archivo. Los planes alojados se miden en minutos de video original, así que un VOD de cuatro horas consume 240 minutos, te quedes con tres clips o con diez.",
      s2Heading: "2. Deja que SupoClip haga la preselección y busca lo que no puede oír",
      s2Body:
        "SupoClip transcribe el stream y propone momentos, cada uno con puntaje de gancho, interacción, valor y compartibilidad. Usa esos puntajes para decidir qué ver primero, no como pronóstico de vistas. Como la selección nace del audio hablado, destaca en persecuciones narradas, reacciones, opiniones y respuestas al chat, pero no detecta una jugada espectacular en silencio.",
      s3Heading: "3. Encuadra al streamer y al gameplay",
      s3BodyBefore:
        "La mayoría de los streams de GTA colocan la webcam en una esquina. Si recortas a 9:16 sin pensar, terminas con una cara gigante o una franja mínima del juego. SupoClip detecta una webcam fija en una esquina y la coloca arriba del gameplay:",
      s3BodyAfter:
        "Aun así, mira el clip entero en tamaño de teléfono. Comprueba que el minimapa o el auto que persigues sigan dentro del encuadre y que los subtítulos no tapen la acción. Si el momento es puro gameplay, un plano más abierto funciona mejor que una cara enorme.",
      s4Heading: "4. Edita pensando en alguien que no vio el directo",
      s4Body:
        "Quien ve el clip en el feed no vio las últimas tres horas. Empieza directo en la situación, deja solo el contexto necesario y corta justo después del remate. Abre el clip en el estudio para recortar o dividir, o escribe una instrucción en la edición con IA — como “corta el silencio antes de la persecución” — y revisa el resultado antes de guardarlo.",
      s5Heading: "5. Corrige los subtítulos",
      s5Body:
        "Los subtítulos vienen sincronizados palabra por palabra, pero la transcripción nunca escuchó la mitad de los nombres de GTA 6. Corrige nombres de personajes y lugares (Lucia, Jason, Leonida, Vice City), usuarios y frases gritadas. Cada clip también incluye un título gancho breve arriba en los primeros segundos; asegúrate de que describa lo que ocurre de verdad, porque prometer algo que el clip no cumple hace perder retención y aprobación en campañas.",
      editorAlt: "Editor de subtítulos de SupoClip con controles de estilo, transcripción palabra por palabra y vista previa de zona segura",
      editorCaption: "El editor de SupoClip mostrado con un video de ejemplo: transcripción, estilo de subtítulos y vista previa lado a lado.",
      s6Heading: "6. Exporta, publica y envía el enlace",
      s6Body:
        "Exporta en 9:16 para TikTok, Shorts o Reels, o publica directo en cuentas conectadas de TikTok y YouTube desde SupoClip. Con Creator puedes programar publicaciones con anticipación, lo que ayuda a mantener un ritmo constante toda la semana de estreno. En campañas, envía cada enlace apenas salga publicado y lleva un registro sencillo: VOD de origen, marca de tiempo, tipo de momento y vistas.",
      scheduleLinkLabel: "Cómo crear y programar una semana de YouTube Shorts con una grabación",
    },
    launchWeek: {
      heading: "Un plan para la semana de estreno",
      lead: "Los primeros días tras el lanzamiento son los más competidos y los más vistos. La velocidad importa, pero un clip flojo subido a las carreras sigue perdiendo contra uno limpio subido una hora después. Un ritmo realista:",
    },
    mistakes: {
      heading: "Errores que les cuestan vistas a los clippers de GTA 6",
      items: [
        {
          label: "Spoilers para rascar alcance.",
          body: "Suben rápido, pero los reportan, los bajan y pueden arrastrar a toda la cuenta.",
        },
        {
          label: "Clips que necesitan haber visto el stream.",
          body: "“Por fin lo logró” no significa nada para un desconocido. Di qué logró.",
        },
        {
          label: "Cortar antes del remate.",
          body: "Si le pides a la gente que espere algo, muéstralo.",
        },
        {
          label: "Encuadre ilegible.",
          body: "HUD diminuto y un recorte que saca el auto de plano. Revisa siempre en tamaño de teléfono.",
        },
        {
          label: "Música con licencia.",
          body: "Un tema de la radio debajo del clip puede dejarlo sin audio o ceder los ingresos a terceros.",
        },
        {
          label: "Cantidad sobre calidad.",
          body: "Veinte clips casi idénticos de la misma persecución compiten entre sí. Publica el mejor.",
        },
      ],
      moreBeforeFirst: "Más sobre cómo detectar momentos que se entienden solos en ",
      moreFirstLink: "cómo encontrar los mejores momentos en un video largo",
      moreBetween: ", y sobre grabaciones de streams en general en ",
      moreSecondLink: "cómo convertir VODs de Twitch en clips para TikTok y Shorts",
      moreAfter: ".",
    },
    sources: {
      heading: "Fuentes",
      gtaSix: "Rockstar Games: Grand Theft Auto VI",
      rockstarPolicy: "Rockstar Games: Política sobre la publicación de material con derechos de autor",
      whopContentRewards: "Whop: Content Rewards",
      youtubeMonetization: "Ayuda de YouTube: Políticas de monetización de canales",
      checkedNote:
        "Revisado el 17 de noviembre de 2026. Las tarifas de campaña y las reglas de cada plataforma cambian; consulta las versiones vigentes antes de depender de ellas.",
    },
    calculator: {
      eyebrow: "Cálculo interactivo",
      title: "Cuánto podría pagar un mes de clips de GTA 6",
      description:
        "Mueve los controles con tus propios supuestos. El pago por clip es vistas aprobadas × tarifa de campaña, frenado en el tope por clip.",
      clipsPerWeekLabel: "Clips publicados por semana",
      clipsPerWeekHint: "Cuenta solo clips de los que realmente estés orgulloso de subir.",
      viewsPerClipLabel: "Vistas aprobadas por clip (promedio)",
      viewsPerClipHint: "Las campañas pagan por las vistas que pasan revisión dentro de su ventana de seguimiento.",
      rateLabel: "Tarifa por cada 1.000 vistas",
      rateHint: "La fija cada campaña. Alrededor de US$ 1 es una referencia habitual.",
      capLabel: "Tope por clip",
      capNone: "Sin tope",
      capHint: "Muchas campañas dejan de pagar un clip al llegar a un monto fijo. 0 significa sin tope.",
      vodHoursLabel: "Horas de stream que recortas al mes",
      vodHoursUnit: "h",
      vodHoursHint: "SupoClip alojado mide minutos de video original, no cantidad de clips.",
      payoutLabel: "Pago estimado",
      payoutDetail: (clipsPerMonth, formattedViews) => `${clipsPerMonth} clips · ${formattedViews} vistas al mes`,
      perClipLabel: "Por clip",
      cappedDetail: "Cada clip llega al tope con este nivel de vistas.",
      uncappedDetail: "Por debajo del tope por clip.",
      afterPlanLabel: "Después del plan SupoClip",
      planFitsDetail: (minutes, planName, formattedPrice, planMinutes) =>
        `${minutes} min de origen entran en ${planName} (${formattedPrice}/mes, ${planMinutes} min).`,
      planOverDetail: (minutes) =>
        `${minutes} minutos superan los 1.500 de Creator. Recorta menos horas o autoaloja.`,
      averageLead: "El promedio oculta la distribución real.",
      averageBody:
        " Un clip con 200.000 vistas y diecinueve con 500 promedian unas 10.500 vistas por clip, pero con un tope de US$ 150 ese mes paga alrededor de US$ 160, no los US$ 210 que sugeriría un reparto parejo. Espera que la mayoría gane poco y unos pocos carguen el mes.",
      disclaimer:
        "Solo es un estimado, antes de impuestos y comisiones. No es una garantía de ingresos, y el presupuesto de una campaña puede agotarse antes de que cuenten tus vistas.",
    },
    workflowDiagram: {
      caption: "El flujo de clipping para GTA 6 · tú decides, SupoClip hace lo repetitivo",
      youLabel: "Tú",
      appLabel: "SupoClip",
      steps: [
        { title: "Elegir VOD", you: "Tu propio stream o uno autorizado por la campaña", app: "Importa enlaces de Twitch, Kick y YouTube o un archivo" },
        { title: "Preselección", you: "Miras candidatos y anotas jugadas silenciosas", app: "Transcribe el audio y ordena momentos hablados" },
        { title: "Encuadrar", you: "Revisas cada cambio de plano en tamaño de teléfono", app: "Coloca una webcam fija en esquina sobre el gameplay" },
        { title: "Cortar", you: "Gancho en los primeros segundos y cierre en el remate", app: "Recortes, divisiones y ediciones con IA en el estudio" },
        { title: "Subtitular", you: "Corriges nombres: Lucia, Jason, Leonida, Vice City", app: "Subtítulos palabra por palabra y título gancho" },
        { title: "Publicar", you: "Envías los enlaces a la campaña y registras resultados", app: "Exporta en 9:16 y publica en TikTok y YouTube" },
      ],
    },
    reactionLayout: {
      svgTitle: "De un stream 16:9 a un clip vertical 9:16 apilado",
      svgDesc:
        "A la izquierda, un stream panorámico con la webcam en la esquina superior derecha. A la derecha, el clip vertical: la webcam ocupa el tercio superior y el gameplay llena los dos tercios inferiores.",
      sourceTitle: "VOD del stream · 16:9",
      sourceSubtitle: "Webcam fija en una esquina superior",
      captionsHere: "aquí van los subtítulos",
      outputTitle: "Clip · 9:16 · 1080×1920",
      figcaption:
        "Cuando SupoClip detecta un rostro estable en una esquina superior del plano, ubica al streamer en el tercio superior y el gameplay debajo. ¿No hay webcam fija o cambia de sitio? Usa sus otros diseños, así que revisa siempre el resultado.",
    },
    clipAnatomy: {
      eyebrow: "Anatomía de un clip",
      title: "Una persecución de 43 segundos en GTA 6 cortada para sostener la atención",
      hookBarNote: "Título gancho en pantalla durante los primeros ~4 s",
      secondsUnit: "s",
      parts: [
        { label: "Gancho", from: 0, to: 3, note: "Di o muestra la situación: “seis estrellas al minuto de empezar”." },
        { label: "Contexto", from: 3, to: 10, note: "Solo lo necesario para que un desconocido siga el hilo." },
        { label: "Desarrollo", from: 10, to: 32, note: "La persecución, el atraco o la discusión con el chat." },
        { label: "Remate", from: 32, to: 40, note: "El choque, el escape o la reacción." },
        { label: "Cierre", from: 40, to: 43, note: "Corta ya. Nada de “bueno, en fin…”" },
      ],
    },
    rulesBoard: {
      caption: "Qué puedes publicar · resumen informativo, no asesoría legal",
      groups: [
        {
          status: "ok",
          label: "Suele estar bien",
          items: [
            "Tu propio gameplay con tus comentarios",
            "Momentos de la historia dentro de una partida narrada",
            "Streams cuyo dueño corre una campaña de clipping, dentro de sus reglas",
          ],
        },
        {
          status: "warn",
          label: "Revisa primero",
          items: [
            "Monetización: la política de Rockstar Games está redactada para uso no comercial",
            "Canciones con licencia de la radio: pueden silenciar o reclamar el video",
            "Un stream ajeno sin campaña ni permiso",
          ],
        },
        {
          status: "no",
          label: "Lo retiran",
          items: [
            "El final o una gran revelación de la historia",
            "Cinemáticas aisladas y recopilaciones de escenas",
            "Filtraciones, trampas y mods no autorizados",
          ],
        },
      ],
    },
    launchWeekTable: {
      caption: "Plan de semana de estreno para clips de GTA 6 (lanzamiento: 19 de noviembre de 2026)",
      headers: ["Cuándo", "Enfoque", "Qué hacer"],
      rows: [
        [
          "Antes del 19 nov",
          "Preparación",
          "Conecta TikTok y YouTube, elige tu estilo de subtítulos y lee las reglas de las campañas a las que quieres entrar. El material previo al estreno se retira, así que no publiques nada.",
        ],
        [
          "19 nov (estreno)",
          "Transmitir o mirar",
          "Juega o sigue los streams que tengas permiso para recortar. Apunta las marcas de tiempo de jugadas silenciosas en el momento en que pasan.",
        ],
        [
          "20 nov",
          "Primera tanda",
          "Importa los VODs del día anterior. Quédate con los 3 a 5 momentos hablados más fuertes y termínalos bien en lugar de subir veinte clips a medias.",
        ],
        [
          "21–23 nov",
          "Ritmo constante",
          "Entra un VOD, salen unos pocos clips, cada día. Evita la historia: los clips con spoilers se reportan y se retiran.",
        ],
        [
          "24–25 nov",
          "Leer los números",
          "Compara qué tipo de momento retuvo mejor a la gente: persecuciones, reacciones, descubrimientos del mapa u opiniones. Haz más de eso.",
        ],
      ],
    },
    momentMap: {
      eyebrow: "Mapa de momentos · stream de ejemplo",
      title: "Un stream de cuatro horas de GTA 6, tres tipos de momento",
      description:
        "Selecciona un marcador. SupoClip lee la transcripción, así que los momentos hablados entran en la preselección; las jugadas en silencio necesitan tus ojos y los spoilers quedan fuera.",
      legendAria: "Leyenda",
      momentsAria: "Momentos del stream",
      kinds: {
        spoken: { label: "Momento hablado: la preselección con IA puede encontrarlo", short: "La IA lo detecta" },
        silent: { label: "Acción silenciosa: revísala tú mismo", short: "Revisar a mano" },
        skip: { label: "Spoiler de historia o cinemática: déjalo fuera", short: "Omitir" },
      },
      moments: [
        { minute: 6, kind: "spoken", title: "Primera reacción a la vista inicial", note: "El streamer cuenta lo que ve y por qué le sorprende. La voz sostiene el momento, así que la transcripción lo detecta." },
        { minute: 31, kind: "skip", title: "Cinemática de historia", note: "Las cinemáticas aisladas están entre lo que la política de videos de Rockstar Games retira. Mantén los momentos de historia dentro de una narración o déjalos fuera." },
        { minute: 58, kind: "silent", title: "Casi choque en la autopista", note: "Una gran jugada sin una sola palabra encima. Una lista basada en voz puede saltársela; anota el minuto mientras miras." },
        { minute: 84, kind: "spoken", title: "El chat pregunta por el tamaño del mapa", note: "Una pregunta más una respuesta clara. Deja la pregunta en pantalla o en la primera frase." },
        { minute: 112, kind: "spoken", title: "Persecución de cinco estrellas narrada", note: "Acción con comentarios en vivo. Mantén el inicio, la persecución y la reacción en un mismo corte." },
        { minute: 139, kind: "silent", title: "Bug de físicas", note: "Gracioso de ver, pero nadie lo explica hablando. Búscalo en la línea de tiempo y encuadra la acción, no la cara." },
        { minute: 171, kind: "spoken", title: "Opinión picante sobre la banda sonora", note: "Una opinión con argumentos. Silencia o reemplaza la música con licencia de la radio antes de publicar." },
        { minute: 203, kind: "skip", title: "Revelación del final de la historia", note: "Publicar el final o un giro importante es justo lo que persigue la política, y termina reportado." },
        { minute: 226, kind: "spoken", title: "Cierre: el mejor momento del día", note: "El streamer resume su propio momento destacado. Útil para un título gancho y para ubicar el instante real." },
      ],
      showTableSummary: "Ver todos los momentos en una tabla",
      tableHeaders: ["Tiempo", "Momento", "Revisión"],
    },
  },
  creatorGuides: {
    "twitch-vod-to-shorts": {
      post: {
        title: "Cómo convertir VODs de Twitch en clips para TikTok y Shorts",
        description:
          "Reutiliza un VOD de Twitch en clips verticales: importa una transmisión terminada, encuentra momentos hablados, revisa el encuadre del gameplay, agrega subtítulos y exporta para TikTok o Shorts.",
        eyebrow: "Clips de streams",
        category: "Guía",
        readingTime: "Lectura de 6 min",
        summary:
          "Reutiliza un VOD de Twitch en clips verticales: importa una transmisión terminada, encuentra momentos hablados, revisa el encuadre del gameplay, agrega subtítulos y exporta para TikTok o Shorts.",
        keywords: ["VOD de Twitch a Shorts", "VOD de Twitch a TikTok", "creador de clips de Twitch", "recortador de streams con IA"],
      },
      intro:
        "Un directo largo reúne momentos muy distintos: anécdotas, reacciones, explicaciones y jugadas que suceden sin decir una palabra. Cada uno requiere decisiones de edición distintas. Usa la preselección con IA para los momentos hablados y luego revisa el gameplay y el encuadre por tu cuenta antes de convertir un VOD de Twitch en clips verticales.",
      sections: [
        {
          id: "import",
          heading: "1. Importa una transmisión terminada",
          paragraphs: [
            "Espera a que termine el directo y usa un enlace de VOD o clip de Twitch, o sube tu propia grabación. SupoClip acepta esas fuentes; no recorta un stream mientras sigue en vivo. Conserva una copia original de las grabaciones que planeas reutilizar.",
            "Si un VOD no está disponible, tiene restricciones o no puede descargarse, usa una grabación local que tengas permiso para editar. Empieza con una grabación manejable en lugar de subir todo tu archivo en un solo intento. Revisa los límites de subida y duración que muestra la app para tu despliegue.",
          ],
        },
        {
          id: "moments",
          heading: "2. Separa los momentos hablados de la acción en silencio",
          paragraphs: [
            "Envía la grabación a analizar y revisa los momentos propuestos. La selección basada en transcripción funciona muy bien para una historia entre partidas, una explicación estratégica o una reacción en la que el comentario deja claro lo que ocurrió.",
            "Una jugada decisiva en silencio o un detalle sutil en pantalla puede no aparecer nunca en la transcripción. Usa marcas de tiempo de tus propias notas para revisar esos pasajes en la grabación original. No interpretes la ausencia de una sugerencia de la IA como prueba de que no pasó nada interesante.",
          ],
          table: {
            caption: "Qué tipo de revisión necesita cada momento de un stream",
            headers: ["Momento", "Qué buscar", "Revisar antes de exportar"],
            rows: [
              ["Historia u opinión", "Una apertura clara y un remate que se sostenga solo", "Elimina referencias al chat previo que la audiencia nueva no puede ver"],
              ["Reacción hablada", "Suficiente contexto previo para entender la reacción", "Mantén el evento y la reacción en el mismo corte"],
              ["Gameplay silencioso", "La jugada real en el material original", "Selecciónalo a mano; el análisis de voz puede pasarlo por alto"],
              ["Respuesta al chat", "La pregunta más una respuesta clara", "Conserva el contexto sin exponer información privada"],
            ],
          },
          links: [
            { href: "/blog/find-best-moments-in-video", label: "Comprueba si un momento se entiende por sí solo" },
            { href: "/blog/how-to-clip-gta-6", label: "¿Recortando streams de GTA 6? Sigue el flujo completo" },
          ],
        },
        {
          id: "frame",
          heading: "3. Mantén la acción importante dentro del encuadre vertical",
          paragraphs: [
            "Un recorte centrado en el rostro sirve muy bien cuando hablas a cámara. Puede ser la elección equivocada cuando quien mira necesita ver el minimapa, el marcador, el inventario o una vista amplia de la jugada. Mira el candidato entero con el formato de destino seleccionado y ajusta el encuadre en el editor.",
            "Si la acción no sobrevive a un recorte cerrado, usa un encuadre que conserve más de la imagen original en lugar de forzar cada momento a un primer plano. Este flujo no promete un diseño automático apilado de webcam y juego para cualquier grabación. Un plano amplio legible funciona mejor para el clip que una cara enorme que tapa el momento clave.",
          ],
          bullets: [
            "Revisa el inicio, la jugada misma y cada cambio de cámara o escena.",
            "Asegúrate de que los subtítulos no tapen la interfaz del juego ni el objetivo principal.",
            "Previsualiza en un teléfono; textos pequeños de la interfaz pueden volverse ilegibles incluso dentro del encuadre.",
          ],
        },
        {
          id: "captions",
          heading: "4. Corrige nombres del juego y recorta el cierre",
          paragraphs: [
            "Los subtítulos automáticos pueden fallar con nombres de usuario, personajes, siglas y palabras gritadas. Corrígelos en la transcripción y vuelve a escuchar el audio. Conserva jerga intencional cuando importa para el sentido, pero no dejes un error de transcripción solo porque suena plausible.",
            "Corta justo después de que la jugada o la explicación se resuelva. Si el clip cierra con “espera a ver qué pasa”, incluye aquello por lo que pediste esperar. Un gancho claro describe la situación real en lugar de prometer algo que el video no respalda.",
          ],
          links: [{ href: "/blog/word-by-word-video-captions", label: "Haz que los subtítulos se lean bien sin tapar la acción" }],
        },
        {
          id: "export",
          heading: "5. Exporta para TikTok o YouTube Shorts",
          paragraphs: [
            "Elige el ajuste de exportación de la plataforma y descarga el video renderizado. Sube la versión de TikTok por tu cuenta o desde las conexiones activas de tu cuenta. En YouTube también puedes publicar desde SupoClip cuando la publicación esté habilitada para tu cuenta; programar publicaciones requiere el plan y acceso correspondientes.",
            "Lleva un registro simple del VOD original, la marca de tiempo, el tema y el destino publicado. Compara por separado el rendimiento de historias, reacciones y explicaciones. Así sabrás qué tipo de momento de tus directos valora tu audiencia en lugar de tratar todos los clips como el mismo experimento.",
          ],
        },
      ],
      faqs: [
        {
          question: "¿Puede SupoClip recortar mi stream de Twitch mientras estoy en directo?",
          answer:
            "No. Usa un VOD o clip cuando termine la transmisión, o sube tu grabación. El flujo que describimos aquí es para video grabado.",
        },
        {
          question: "¿La IA detectará todos los momentos destacados de gaming?",
          answer:
            "No. SupoClip usa la transcripción para elegir momentos, así que jugadas silenciosas importantes pueden quedar fuera. Revisa los eventos visuales a mano y verifica el recorte final.",
        },
        {
          question: "¿Este flujo publica los clips directamente en TikTok?",
          answer:
            "Esta guía trabaja con exportaciones descargadas para TikTok. No asumas que la publicación directa en TikTok está habilitada en todos los casos; revisa las plataformas conectadas y el acceso que muestra tu cuenta.",
        },
      ],
      cta: {
        title: "Dale una segunda vida a tu próximo VOD",
        body: "Importa un stream terminado, revisa los momentos hablados y exporta los fragmentos que funcionan fuera del directo.",
        label: "Recortar mi VOD de Twitch",
      },
      gallery: {
        heading: "Qué demuestra un clip guiado por la voz",
        intro:
          "Esta fuente es una entrevista grabada, no un directo de Twitch ni una demo de detección de gameplay. Úsala para revisar el flujo de momentos hablados descrito arriba; una jugada silenciosa o un diseño de webcam sobre juego requiere su propia revisión visual.",
        notes: {
          "ton-2":
            "Aquí las palabras llevan toda la idea, lo que lo convierte en un buen ejemplo de selección por transcripción. En un stream, aplica la misma prueba de contexto a una historia o explicación y luego revisa aparte que la acción del juego siga visible.",
        },
      },
    },
    "podcast-to-shorts": {
      post: {
        title: "Cómo convertir un videopódcast en Shorts y Reels",
        description:
          "Convierte un videopódcast en Shorts y Reels con subtítulos: elige momentos completos, ajusta el encuadre de quienes hablan, revisa los subtítulos y exporta clips con SupoClip.",
        eyebrow: "Flujo para creadores",
        category: "Guía",
        readingTime: "Lectura de 7 min",
        summary:
          "Convierte un videopódcast en Shorts y Reels con subtítulos: elige momentos completos, ajusta el encuadre de quienes hablan, revisa los subtítulos y exporta clips con SupoClip.",
        keywords: ["pódcast a Shorts", "clips de pódcast", "pódcast a Reels", "recortador de videopódcast"],
        imageAlt: "Ton Roosendaal en la entrevista original usada para el ejemplo de recorte de conversación en SupoClip",
      },
      intro:
        "Un buen clip de pódcast entrega una idea completa a alguien nuevo sin pedirle que vea primero el episodio entero. Empieza con una pregunta, una observación llamativa o una lección concreta, y conserva la explicación que sostiene el remate. Aquí tienes una forma repetible de lograrlo con un videopódcast, desde la grabación original hasta la exportación vertical.",
      sections: [
        {
          id: "prepare",
          heading: "1. Empieza con una grabación que puedas reutilizar",
          paragraphs: [
            "Usa la versión en video de tu episodio: un enlace de YouTube o un archivo de video que puedas subir. Una pista de voz clara importa más que un set sofisticado. Si el audio suena turbio, corrígelo antes de pedirle a un sistema de transcripción que encuentre buenos momentos.",
            "Elige un episodio con respuestas o historias bien definidas. Una charla sobre un error concreto, una decisión o un resultado se convierte en clip independiente mucho más fácil que una introducción larga. Asegúrate de tener permiso para reutilizar la grabación y el material de invitados. Este flujo asume video; un episodio solo de audio necesita primero un tratamiento visual.",
          ],
          bullets: [
            "Guarda el título del episodio, el nombre del invitado y el enlace original en las notas del proyecto.",
            "Revisa la duración del original frente a tu plan alojado antes de enviar un episodio largo.",
            "Si un enlace de origen no se puede importar, sube tu propio archivo de video original.",
          ],
        },
        {
          id: "select",
          heading: "2. Genera candidatos y elige ideas completas",
          paragraphs: [
            "Pega el enlace del episodio en SupoClip o sube el archivo. La app transcribe el habla y propone fragmentos cortos. Usa los puntajes como orden de revisión: un puntaje alto te dice qué candidato ver primero, no cuántas vistas va a conseguir.",
            "Reproduce cada candidato separado del resto de la charla. ¿Se identifica el tema desde la primera frase? ¿La respuesta entrega algo concreto? Un clip que arranca con “ese era el problema” necesita una frase previa o un punto de inicio distinto. Recorta saludos y rodeos repetidos, pero conserva cualquier matiz que cambie el sentido de lo que dijo quien habla.",
          ],
          links: [{ href: "/blog/find-best-moments-in-video", label: "Usa la lista de verificación para elegir momentos" }],
        },
        {
          id: "example",
          heading: "Ejemplo práctico: recortar una entrevista grabada",
          paragraphs: [
            "Una entrevista grabada tiene el mismo problema de contexto que un videopódcast: una respuesta puede depender de una pregunta o de una charla anterior. Para los ejemplos de abajo procesamos en SupoClip una entrevista Creative Commons con Ton Roosendaal, fundador de Blender, y renderizamos fragmentos completos con subtítulos.",
            "Usa los enlaces de origen para comparar cada corte con el original. Escucha si el tema queda claro, sigue la explicación y revisa qué quedó fuera del fragmento. Son ejemplos del flujo de recorte, no una afirmación de que el entrevistado use o recomiende SupoClip.",
          ],
        },
        {
          id: "edit",
          heading: "3. Revisa el encuadre y los subtítulos juntos",
          paragraphs: [
            "Abre el clip elegido en el editor. Usa encuadre vertical para Shorts y Reels y mira el corte completo. Un recorte que funciona en el anfitrión puede dejar fuera al invitado tras un cambio de cámara. Cuando importa un gráfico o un plano de dos personas, conserva suficiente cuadro original para que se entienda.",
            "Corrige nombres, marcas, cifras y puntuación en la transcripción. Mantén los subtítulos lejos de los rostros y de los controles de cada plataforma. Previsualiza en tamaño de teléfono en lugar de juzgar la lectura en un monitor de escritorio. Si el estilo de los subtítulos distrae de la respuesta, simplifica la animación y resalta menos palabras.",
          ],
          links: [{ href: "/blog/word-by-word-video-captions", label: "Diseña y corrige subtítulos palabra por palabra" }],
        },
        {
          id: "publish",
          heading: "4. Exporta cada clip con una razón clara para verlo",
          paragraphs: [
            "Exporta con el ajuste de tu plataforma destino. Ponle a cada clip un título que describa la idea real que se lleva quien lo ve. Evita un gancho exagerado que el invitado nunca sostiene: la gente debe encontrar la respuesta prometida dentro del clip.",
            "Sube el archivo exportado a Reels o publica en YouTube desde SupoClip cuando la publicación esté habilitada en tu cuenta. La programación depende de tu plan y del acceso de publicación configurado. Deja el enlace al episodio completo en la descripción o en el perfil para que quien quiera siga viendo.",
          ],
          bullets: [
            "Mira el archivo exportado completo, incluidos el primer y el último segundo.",
            "Comprueba que el texto importante no quede tapado por los botones de la app destino.",
            "Registra de qué episodio y qué minuto original salió cada clip.",
            "Revisa los datos reales de vistas y comentarios antes de decidir qué temas recortar después.",
          ],
          links: [{ href: "/blog/schedule-week-of-youtube-shorts", label: "Arma una semana de Shorts desde una grabación" }],
        },
      ],
      faqs: [
        {
          question: "¿Puedo crear Reels a partir de un pódcast en YouTube?",
          answer:
            "Sí. Si tienes permiso para reutilizarlo, usa el video de YouTube como fuente, crea el clip vertical y descarga la exportación para Reels. Sube ese archivo a Instagram; esta guía no asume que haya publicación directa a Instagram.",
        },
        {
          question: "¿Cuántos clips debería sacar de un episodio?",
          answer:
            "Quédate con todos los que contengan ideas distintas y completas que valga la pena publicar. No hay un número fijo por episodio, y repetir la misma respuesta con otro gancho no crea una idea nueva.",
        },
        {
          question: "¿Un clip corto puede omitir la pregunta del entrevistador?",
          answer:
            "Sí, cuando la respuesta nombra su propio tema y se entiende sola. Si no, conserva una versión breve de la pregunta o elige un punto de inicio anterior que mantenga el contexto.",
        },
      ],
      cta: {
        title: "Convierte tu próximo episodio de pódcast en clips",
        body: "Trae un episodio en video. Revisa los momentos sugeridos, corrige los subtítulos y exporta los clips que de verdad publicarías. SupoClip alojado es de pago; el autoalojamiento es otra opción.",
        label: "Recortar mi pódcast",
      },
      gallery: {
        heading: "De una conversación grabada a una respuesta completa",
        intro:
          "Estos ejemplos usan una entrevista con Ton Roosendaal, fundador de Blender. Es una entrevista y no un episodio de pódcast, pero la prueba editorial es la misma: ¿la respuesta presenta el tema y entrega una idea útil sin la charla que la rodea?",
        notes: {
          "ton-1":
            "Presta atención al tema en la primera oración y sigue la explicación hasta su cierre. Compara el corte con el original para comprobar que el sentido del hablante sobrevive al formato corto.",
          "ton-2":
            "Revisa la segunda respuesta como una publicación independiente, no como continuación. Los subtítulos y el encuadre deben ayudar a alguien nuevo a entenderla sin haber visto el primer clip.",
        },
      },
    },
    "find-best-moments-in-video": {
      post: {
        title: "Cómo encontrar los mejores momentos en un video largo",
        description:
          "Elige mejores momentos destacados con una lista práctica para gancho, contexto, remate y cortes limpios. Usa sugerencias de IA sin depender de un puntaje de viralidad.",
        eyebrow: "Flujo para creadores",
        category: "Guía",
        readingTime: "Lectura de 6 min",
        summary:
          "Elige mejores momentos destacados con una lista práctica para gancho, contexto, remate y cortes limpios. Usa sugerencias de IA sin depender de un puntaje de viralidad.",
        keywords: ["encontrar mejores momentos en un video", "elegir clips de video", "detección de momentos con IA", "elegir clips de pódcast"],
      },
      intro:
        "El mejor momento de una grabación no siempre es el más ruidoso, gracioso o emotivo. Para un video corto, también tiene que sostenerse fuera de su contexto original. Usa las verificaciones de abajo para convertir una grabación larga en una lista corta de momentos que cualquier espectador nuevo pueda entender.",
      sections: [
        {
          id: "brief",
          heading: "Define a quién debe ayudar el clip",
          paragraphs: [
            "Escribe una sola frase antes de buscar: por ejemplo, “Este clip debe ayudar a quien funda por primera vez a manejar una charla difícil con un cliente”. Una audiencia acotada te da una regla real de selección. Sin ella, cualquier frase llamativa parece un buen clip.",
            "Recorre capítulos, preguntas y cambios de tema buscando respuestas a ese objetivo. Los buenos candidatos suelen incluir un error concreto, una explicación de antes y después, una demostración o un desacuerdo bien argumentado. Anota tiempos aproximados; afinarás los cortes después.",
          ],
        },
        {
          id: "checklist",
          heading: "Aplica cuatro revisiones antes de quedarte con un momento",
          paragraphs: [
            "Mira cada candidato como si apareciera en un feed sin título del episodio. Cada revisión responde una pregunta distinta. Un buen inicio no compensa una explicación ausente, y una explicación completa igual puede necesitar una primera frase más limpia.",
          ],
          table: {
            caption: "Lista editorial para un momento destacado independiente",
            headers: ["Revisión", "Déjalo cuando", "Corrígelo cuando"],
            rows: [
              ["Gancho", "El inicio nombra un problema, una tensión o un resultado concreto", "Empieza con saludos o un “esto” y “eso” sin explicar"],
              ["Contexto", "El tema y las condiciones relevantes quedan claros", "Necesitas los cinco minutos anteriores para entenderlo"],
              ["Remate", "Quien habla entrega una razón, un ejemplo o una respuesta útil", "El clip promete algo pero corta antes de la respuesta"],
              ["Límites", "El clip empieza y termina con ideas completas", "Un corte cambia el sentido o interrumpe la conclusión"],
            ],
          },
          bullets: [
            "Escúchalo una vez sin ver la imagen: ¿el argumento sigue siendo coherente?",
            "Míralo una vez sin sonido: ¿el encuadre y los subtítulos sostienen el tema?",
            "Revisa la grabación original alrededor de ambos cortes por si eliminaste una aclaración clave.",
          ],
        },
        {
          id: "example",
          heading: "Compara una idea completa con una frase aislada",
          paragraphs: [
            "Una afirmación general sobre practicar una habilidad puede sonar como una cita atractiva mientras deja fuera la explicación útil. Un corte más sólido incluye la acción que recomienda quien habla y suficiente razón detrás. La grabación original alrededor de ambos extremos es la referencia para verificar que la versión corta siga siendo fiel.",
            "Los ejemplos de Andrew Price de abajo salen de la misma presentación conocida. Aplica la lista a cada uno: ¿el fragmento conserva la explicación que hace útil su apertura? Si un candidato necesita más contexto del que cabe en un clip corto, déjalo en la grabación completa.",
          ],
        },
        {
          id: "ai",
          heading: "Deja que la IA acorte la búsqueda y decide tú",
          paragraphs: [
            "Un recortador de video con IA puede transcribir la grabación, proponer segmentos candidatos y ordenarlos para revisión. En SupoClip, los puntajes de gancho, interacción, valor y compartibilidad ayudan a ordenar esa lista corta. Son señales editoriales, no vistas pronosticadas.",
            "El análisis basado en transcripción funciona mejor cuando el momento interesante se expresa con palabras. Una demostración silenciosa, una revelación visual o una jugada ganadora pueden valer mucho sin narrarse en voz alta. Revisa esos momentos por tu cuenta en lugar de asumir que la búsqueda por texto encontró todo.",
          ],
          links: [{ href: "/ai-video-clipper", label: "Mira qué automatiza el recortador de video con IA" }],
        },
        {
          id: "refine",
          heading: "Afina el corte antes de sumar efectos",
          paragraphs: [
            "Coloca el inicio justo antes de la primera idea completa que importa. Recorta introducciones repetidas cuando puedas y reproduce el final junto con la frase anterior. Termina justo después del remate en vez de arrastrar el tema siguiente dentro del clip. Conserva pausas que ayudan a entender la respuesta.",
            "Solo después ajusta subtítulos, encuadre y título gancho. Guarda una nota breve sobre por qué elegiste ese momento. Tras publicar, compara la respuesta de la audiencia con esa hipótesis. Si la gente se va antes de la explicación, prueba un inicio más claro; si lo ve pero no reacciona, replantea si la idea realmente sirve al público que elegiste.",
          ],
          links: [
            { href: "/blog/podcast-to-shorts", label: "Aplica esta lista a un videopódcast" },
            { href: "/blog/repurpose-webinar-clips", label: "Aplícala a la grabación de un webinar" },
          ],
        },
      ],
      faqs: [
        {
          question: "¿Un puntaje alto de viralidad garantiza un clip exitoso?",
          answer:
            "No. Los puntajes ayudan a priorizar la revisión, pero la distribución, el encaje con la audiencia, la edición final y la grabación original siguen pesando. Evalúa el rendimiento con resultados reales de publicación.",
        },
        {
          question: "¿Todos los momentos destacados deben durar lo mismo?",
          answer:
            "No. Quédate con el corte más breve que entregue una idea completa y fiel. Revisa aparte los requisitos de formato vigentes en cada plataforma.",
        },
        {
          question: "¿Qué pasa si el mejor momento es sobre todo visual?",
          answer:
            "Revísalo a mano. El descubrimiento basado en transcripción puede pasar por alto eventos que no se narran hablando. Elige un encuadre que conserve la acción visual en vez de asumir que un recorte en la cara siempre sirve.",
        },
      ],
      cta: {
        title: "Encuentra tu preselección en tu propia grabación",
        body: "Usa SupoClip para proponer momentos y luego aplica las revisiones de gancho, contexto, remate y límites antes de exportar.",
        label: "Encontrar momentos de mi video",
      },
      gallery: {
        heading: "Dos ideas distintas de la conocida charla de Andrew Price",
        intro:
          "La charla original tenía 3,8 millones de vistas en YouTube al seleccionarla el 26 de octubre de 2026. Son renderizados nuevos de SupoClip, no capturas de Shorts ajenos. Mira los ejemplos completos y usa los enlaces originales para revisar sus cortes.",
        notes: {
          "price-1":
            "Aplica las cuatro revisiones: identifica la idea de inicio, el contexto que la hace precisa, la explicación que justifica el remate y el punto exacto donde termina.",
          "price-2":
            "Compara esta idea con el primer clip. Mantener un aporte distinto importa más que llenar una cuota con varias versiones de la misma respuesta.",
        },
      },
    },
    "word-by-word-video-captions": {
      post: {
        title: "Cómo agregar subtítulos palabra por palabra a videos cortos",
        description:
          "Crea subtítulos palabra por palabra fáciles de leer para Shorts, Reels y TikTok. Corrige la transcripción, elige un estilo, revisa las zonas seguras y exporta con SupoClip.",
        eyebrow: "Flujo para creadores",
        category: "Guía",
        readingTime: "Lectura de 6 min",
        summary:
          "Crea subtítulos palabra por palabra fáciles de leer para Shorts, Reels y TikTok. Corrige la transcripción, elige un estilo, revisa las zonas seguras y exporta con SupoClip.",
        keywords: ["subtítulos palabra por palabra", "subtítulos animados", "agregar subtítulos a Shorts", "subtítulos automáticos para video"],
        imageAlt: "Editor de SupoClip con estilos de subtítulos, sincronización por palabra y vista previa de video vertical",
      },
      intro:
        "Los subtítulos palabra por palabra resaltan lo que se dice al ritmo exacto de la voz. Solo suman cuando las palabras son precisas, se leen con facilidad y están donde quien mira puede verlas. Diseña el estilo alrededor de tu grabación en lugar de elegir primero la animación más llamativa.",
      sections: [
        {
          id: "transcribe",
          heading: "1. Genera un clip y revisa su transcripción",
          paragraphs: [
            "Importa un enlace compatible o sube un video a SupoClip. Cuando se genere el clip, ábrelo en el editor y revisa la transcripción mientras lo reproduces. Las marcas de tiempo por palabra conectan el texto con el audio.",
            "Empieza por la precisión. Revisa nombres, números, términos de producto y palabras dichas sobre música u otra voz. Escucha cualquier frase dudosa dentro de su oración completa antes de cambiarla. Los subtítulos deben reflejar lo que se dijo de verdad, sin convertir una frase ambigua en una afirmación más tajante.",
          ],
        },
        {
          id: "correct",
          heading: "2. Corrige las palabras antes de darles estilo",
          paragraphs: [
            "Usa los controles de transcripción para corregir una palabra mal entendida y reproduce ese tramo otra vez. Corregir la ortografía no debería exigir mover un tiempo que ya estaba bien. Si reescribes un bloque más largo, revisa la sincronización resultante en vez de asumir que el texto nuevo encajará en la cadencia anterior.",
            "Elimina errores de transcripción que distraen, pero conserva matices que cambian el significado. Como ejemplo ilustrativo, “quince por ciento” y “cincuenta por ciento” pueden parecer igual de plausibles en texto; el audio decide cuál va en el clip final.",
          ],
          example: "editor",
        },
        {
          id: "style",
          heading: "3. Elige un estilo de subtítulos legible",
          paragraphs: [
            "Parte de una plantilla de subtítulos y ajusta fuente, tamaño, colores, posición y resaltado de la palabra activa. Mantén una paleta breve y consistente. Resaltar cada palabra con un cambio de color agresivo puede competir con una demostración o una escena con mucho movimiento.",
            "Comprueba el fotograma menos favorable, no solo una toma limpia. Un texto blanco que funciona sobre una camiseta oscura puede desaparecer cuando la cámara corta a una diapositiva clara. Un fondo con contraste o un contorno ayuda, pero debe dejar visible al sujeto. Guarda un estilo solo después de verlo a lo largo de un clip representativo.",
          ],
          bullets: [
            "Usa una fuente que siga siendo legible en tamaño de teléfono.",
            "Divide los subtítulos en bloques cortos y fáciles de leer en lugar de llenar todo el ancho.",
            "Mantén el texto importante lejos de rostros, gráficos y controles del producto.",
            "Aplica un estilo guardado de forma consistente y revisa excepciones en cada clip.",
          ],
        },
        {
          id: "safe-area",
          heading: "4. Previsualiza frente a los controles de cada plataforma",
          paragraphs: [
            "Las apps de video corto colocan elementos de interfaz alrededor y encima del video. Usa la vista previa de zona segura del editor como referencia y luego revisa el clip exportado en la app real antes de publicar. No existe una posición única de subtítulos que sirva para todas las apps, pantallas y grabaciones.",
            "Presta especial atención a la zona inferior de la descripción y a los botones laterales. Si el video original ya tiene texto en pantalla, mueve o simplifica los subtítulos para que no se encimen dos mensajes distintos. La posición correcta puede cambiar al pasar de un recorte en el rostro a una grabación de pantalla más amplia.",
          ],
          table: {
            caption: "Problemas de subtítulos que conviene revisar al reproducir",
            headers: ["Problema", "Qué revisar", "Ajuste"],
            rows: [
              ["Las palabras desaparecen", "Cambios entre escenas claras y oscuras", "Sube el contraste o usa un fondo legible"],
              ["Los subtítulos se sienten tarde", "La palabra hablada frente a su resaltado", "Revisa el tiempo en la transcripción y el render final"],
              ["El texto tapa la acción", "Rostros, manos, diapositivas e interfaz del juego", "Mueve los subtítulos o muestra menos texto en pantalla"],
              ["Demasiado movimiento", "Animación durante pasajes hablados rápidos", "Elige un estilo más sobrio y grupos más cortos de palabras"],
            ],
          },
        },
        {
          id: "export",
          heading: "5. Exporta y mira el video sin sonido",
          paragraphs: [
            "Elige el ajuste de destino y renderiza el clip. Los subtítulos renderizados por SupoClip forman parte de la imagen del video, así que siguen visibles al subir el archivo. Mira la versión exportada con sonido para revisar la sincronización y luego sin sonido para confirmar que el tema y el argumento siguen claros.",
            "Los subtítulos incrustados no pueden desactivarse como una pista de subtítulos separada. Tampoco reemplazan todas las funciones de accesibilidad que ofrece una plataforma. Si agregas subtítulos propios de la plataforma, verifica que ambos textos no se dupliquen de forma confusa.",
          ],
          links: [
            { href: "/youtube-shorts-clipper", label: "Termina y exporta un YouTube Short" },
            { href: "/blog/podcast-to-shorts", label: "Usa subtítulos en un flujo de clips de pódcast" },
          ],
        },
      ],
      faqs: [
        {
          question: "¿Puedo corregir los subtítulos generados automáticamente?",
          answer:
            "Sí. Revisa la transcripción en el editor de clips, corrige las palabras mal entendidas y comprueba la reproducción antes de exportar. Vuelve a revisar la sincronización después de cambios grandes en el texto.",
        },
        {
          question: "¿Necesito un estilo diferente para cada plataforma?",
          answer:
            "Puedes reutilizar el mismo estilo visual, pero revisa ubicación y legibilidad en cada destino. Los controles de cada plataforma y el propio video pueden requerir posiciones distintas.",
        },
        {
          question: "¿La audiencia podrá desactivar estos subtítulos?",
          answer:
            "Los subtítulos renderizados en la imagen del video permanecen siempre visibles. Son distintos de una pista de subtítulos separada que la plataforma permite activar o desactivar.",
        },
      ],
      cta: {
        title: "Haz que tu próximo clip sea más fácil de seguir",
        body: "Genera un clip, corrige la transcripción y crea un estilo de subtítulos que se lea bien en el teléfono.",
        label: "Crear un clip con subtítulos",
      },
      gallery: {
        heading: "Lee los subtítulos terminados mientras habla el ponente",
        intro:
          "Estos clips completos se transcribieron y renderizaron con SupoClip. Reprodúcelos con sonido para verificar la sincronización y luego silencia el reproductor para evaluar la legibilidad frente al fondo cambiante.",
        notes: {
          "price-1":
            "Sigue el resaltado de la palabra activa durante una oración completa. Evalúa el texto en el ancho de teléfono mostrado en lugar de agrandar el reproductor para que una letra pequeña parezca legible.",
          "ton-1":
            "Usa una segunda voz y otra grabación para probar el mismo estilo de subtítulos. Reutilizar una plantilla igual exige revisar nombres, contraste, saltos de línea y ubicación en cada clip.",
        },
      },
      editorFigure: {
        ariaLabel: "Abrir la captura a tamaño completo del editor de SupoClip",
        alt: "Editor de subtítulos de SupoClip con controles de estilo, transcripción por palabra y vista previa de zona segura",
        caption:
          "Captura del editor con un video de ejemplo. Los controles de subtítulos y la transcripción aparecen junto a la vista previa; abre la imagen para verlos a tamaño completo.",
      },
    },
  },
};
