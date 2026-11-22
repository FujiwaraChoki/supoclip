import type { TranslatedGuideBundle } from "./types";

export const idGuideBundle: TranslatedGuideBundle = {
  locale: "id",
  shell: {
    seeHostedPlans: "Lihat paket hosted",
    faqTocLabel: "FAQ",
  },
  clipExamplesChrome: {
    eyebrow: "Dibuat dengan SupoClip",
    fullClipMeta: (durationSeconds, sourceRange) => `Klip penuh ${durationSeconds} dtk · sumber ${sourceRange}`,
    videoAriaLabel: (clipTitle, sourceTitle) => `${clipTitle} — contoh SupoClip dari ${sourceTitle}`,
    sourcePrefix: "Sumber:",
    watchOriginal: "Tonton momen aslinya",
    detailsSummary: "Sumber, lisensi, dan editan",
    viewsNote: (formattedViews, checkedAt) => `${formattedViews} tayangan YouTube saat dipilih pada ${checkedAt}.`,
    licenseRecord: "Catatan sumber dan lisensi",
    noEndorsement: "Contoh editorial ini tidak menyiratkan dukungan dari pembicara maupun penerbit sumber aslinya.",
    downloadAttribution: "Unduh catatan atribusi dan pemrosesan",
    footerNote:
      "Ini adalah klip contoh utuh yang dikodekan di 540×960 untuk halaman ini. Stempel waktu sumber merujuk ke rekaman asli; jeda yang dihapus bisa membuat durasi klip akhir lebih pendek dari rentang tersebut.",
  },
  gta6: {
    post: {
      title: "Cara Membuat Klip GTA 6 (dan Berapa Bayarannya yang Sebenarnya)",
      description:
        "Ubah stream dan VOD GTA 6 jadi TikTok dan Shorts: berapa bayaran kampanye clipping, aturan Rockstar Games, dan alur langkah demi langkah dari VOD ke klip vertikal ber-subtitle.",
      eyebrow: "Clipping GTA 6",
      category: "Panduan",
      readingTime: "10 mnt baca",
      summary:
        "Semua orang sedang membuat klip GTA 6. Ini gambaran bayaran sebenarnya, klip seperti apa yang kena takedown, dan alur kerja dari stream empat jam jadi klip vertikal yang layak tayang.",
      keywords: [
        "cara membuat klip GTA 6",
        "klip GTA 6",
        "monetisasi klip GTA 6",
        "kampanye clipping GTA 6",
        "klip GTA 6 TikTok",
        "potong stream GTA 6",
      ],
      imageAlt: "Ilustrasi stream bernuansa senja ala GTA dengan webcam di sudut yang berubah menjadi tiga klip vertikal ber-caption",
    },
    breadcrumb: "Cara membuat klip GTA 6",
    toc: {
      whyNow: "Kenapa GTA 6",
      whatItPays: "Berapa bayarannya",
      rules: "Aturannya",
      workflow: "Alur kerjanya",
      launchWeek: "Minggu rilis",
      mistakes: "Kesalahan umum",
      faq: "FAQ",
    },
    faqs: [
      {
        question: "Benarkah bisa dapat uang dari bikin klip GTA 6?",
        answer:
          "Sebagian clipper memang menghasilkan uang, terutama lewat kampanye yang membayar per 1.000 tayangan yang disetujui, sering kali sekitar $1. Sebagian besar klip menghasilkan sangat sedikit sementara segelintir klip menopang total bulanan, anggaran kampanye bisa habis, dan banyak kampanye membatasi bayaran maksimal per klip. Anggap ini latihan editing berbayar, bukan penghasilan pasti.",
      },
      {
        question: "Apakah legal memposting klip GTA 6?",
        answer:
          "Kebijakan video Rockstar Games mengizinkan video penggemar untuk penggunaan nonkomersial dan memberi Take-Two hak menghapus rekaman kapan saja. Aturan ini secara khusus menyorot akhir cerita, spoiler besar, cutscene yang dipotong terpisah, dan rekaman pra-rilis. Monetisasi tidak diberikan secara eksplisit, jadi baca kebijakan tersebut dan aturan kampanye sebelum mengandalkan penghasilannya.",
      },
      {
        question: "Bisakah SupoClip memotong stream GTA 6 saat masih live?",
        answer:
          "Tidak. SupoClip bekerja pada video rekaman: VOD atau klip Twitch maupun Kick, video YouTube, atau file yang kamu unggah. Potong VOD-nya setelah stream selesai.",
      },
      {
        question: "Apakah AI akan menemukan semua momen keren di GTA 6?",
        answer:
          "Tidak. SupoClip memilih momen dari ucapan, jadi kejar-kejaran yang dinarasikan, reaksi, dan opini akan masuk daftar saran. Momen nyaris tabrakan tanpa suara atau bug fisika bisa terlewat. Catat stempel waktunya saat menonton lalu potong manual.",
      },
      {
        question: "Berapa banyak klip GTA 6 yang didapat dari satu stream?",
        answer:
          "Daftar saran SupoClip bertambah mengikuti durasi video, kira-kira satu kandidat per lima menit hingga batas tertentu. Ambil hanya yang kuat berdiri sendiri; beberapa klip yang rapi jauh lebih baik daripada dua puluh klip kasar.",
      },
      {
        question: "Bagaimana dengan musik radio di dalam game?",
        answer:
          "Stasiun radio GTA memutar lagu berlisensi. Lagu-lagu itu bisa membuat klip dibisukan atau terkena klaim hak cipta di TikTok dan YouTube, terpisah dari kebijakan Rockstar Games sendiri. Potong menghindari lagunya atau kecilkan volume musik bila memungkinkan.",
      },
    ],
    cta: {
      title: "Buat klip dari stream GTA 6 pertamamu",
      body: "Tempel VOD Twitch, Kick, atau YouTube yang sudah selesai. Tinjau daftar saran, rapikan framing dan subtitle, lalu posting klip yang kamu sendiri betah menontonnya.",
      label: "Potong VOD GTA 6",
    },
    heroCaption: "Ilustrasi orisinal. SupoClip tidak berafiliasi dengan Rockstar Games atau Take-Two Interactive.",
    intro: [
      "Feed kamu pasti penuh dengan video seperti ini: wajah streamer di atas, kejar-kejaran GTA di bawah, caption bergerak kata demi kata, dan di kolom komentar ada yang bertanya berapa bayaran clipper-nya. Membuat klip GTA 6 jelas jadi peluang paling ramai musim ini, dan memang membayar sebagian orang — terutama mereka yang memperlakukannya seperti pekerjaan editing, bukan tiket lotre.",
      "Panduan ini membahas alur lengkapnya: dari mana bahan klip berasal, berapa bayaran kampanye yang sebenarnya, aturan apa yang membuat video diturunkan, dan cara mengubah VOD empat jam menjadi beberapa klip vertikal siap tayang dengan SupoClip. Alat visual di sepanjang halaman ini interaktif; ubah angkanya sesuai asumsimu sendiri.",
    ],
    whyNow: {
      heading: "Kenapa semua orang sedang membuat klip GTA 6 sekarang",
      p1LinkText: "Grand Theft Auto VI",
      p1AfterLinkBeforeDate: " meluncur pada ",
      p1Date: "19 November 2026",
      p1AfterDate:
        " di PlayStation 5 dan Xbox Series X|S, dengan versi PC direncanakan menyusul. Gim ini berlatar di negara bagian Leonida di sekitar Vice City dan mengikuti Lucia serta Jason. Minggu peluncurannya berpotensi menjadi salah satu pekan stream terbesar tahun ini, dan sebagian besar orang akan menikmatinya lewat klip pendek, bukan VOD empat jam.",
      p2BeforeStrong: "Peluangnya sederhana dalam satu kalimat: ",
      p2Strong: "stream itu panjang, sedangkan feed butuh 30 sampai 60 detik",
      p2AfterStrong:
        ". Harus ada yang menemukan momennya, memotongnya agar orang asing langsung paham, membingkainya untuk layar ponsel, dan memberi subtitle. Streamer, brand, dan agensi membayar clipper untuk mengerjakannya, sementara kreator yang bermain sendiri melakukannya untuk menumbuhkan channel mereka.",
    },
    whatItPays: {
      heading: "Berapa bayaran sebenarnya dari membuat klip GTA 6",
      lead: "Ada dua cara menghasilkan uang dari klip GTA 6, dan cara kerjanya sangat berbeda.",
      li1Label: "Kampanye clipping.",
      li1BeforeLink: " Streamer atau brand menyediakan anggaran di marketplace seperti ",
      li1LinkText: "Whop Content Rewards",
      li1AfterLink:
        ", menetapkan tarif per 1.000 tayangan, serta aturan penggunaan rekaman. Kamu memposting klip di akunmu sendiri, mengirimkan tautannya, lalu dibayar untuk tayangan yang lolos peninjauan. Whop menyebut rata-ratanya sekitar $1 per 1.000 tayangan; tiap kampanye GTA menetapkan tarif masing-masing, sering kali dengan batas maksimal per klip, dan berhenti membayar saat anggaran habis.",
      li2Label: "Channel milikmu sendiri.",
      li2BeforeLink:
        " Jika kamu bermain dan memberi komentar sendiri, klip menarik penonton ke stream-mu dan bisa ikut program monetisasi platform. ",
      li2LinkText: "Kebijakan monetisasi YouTube",
      li2AfterLink:
        " mengecualikan konten daur ulang tanpa komentar atau penyuntingan yang bermakna, jadi sekadar mengunggah ulang gameplay orang lain mentah-mentah bukan rencana yang tepat.",
      mathIntro:
        "Hitungan kampanye cukup sederhana sekaligus realistis. Di tarif $1 per 1.000 tayangan, 100.000 tayangan yang disetujui bernilai $100. Coba masukkan angkamu di bawah ini, termasuk biaya paket SupoClip yang kamu perlukan:",
    },
    rules: {
      heading: "Aturan yang membuat klip GTA 6 diturunkan",
      p1BeforeLink: "Rockstar Games menerbitkan ",
      p1LinkText: "kebijakan tentang penayangan materi berhak cipta miliknya",
      p1BetweenLinkAndEm: ". Kebijakan ini mengizinkan video penggemar untuk penggunaan ",
      p1Em: "nonkomersial",
      p1AfterEm:
        ", yang diartikan sebagai tidak menghasilkan uang dari rekaman tersebut, serta mempertahankan hak untuk menghapus konten apa pun. Aturan itu secara spesifik menyorot video akhir cerita atau spoiler besar, kompilasi cutscene yang diedit, dan rekaman sebelum rilis. Cutscene yang muncul sebagai bagian dari playthrough panjang bernarasi masih diperbolehkan.",
      p2: "Di atas itu semua, tiap kampanye punya aturan sendiri: stream mana yang boleh dipakai, tag atau tautan wajib, durasi klip minimum, dan larangan trik seperti beli views. Langgar satu aturan saja, tayangan tidak dihitung. Baca aturannya sebelum mulai memotong, bukan sesudahnya.",
    },
    workflow: {
      heading: "Alur kerja: dari VOD GTA 6 menjadi klip siap posting",
      lead: "Ini gambaran utuh prosesnya. Pembagian kerjanya penting: SupoClip menangani bagian yang berulang (transkripsi, daftar saran, crop, dan timing subtitle) supaya fokusmu tertuju pada keputusan editorial yang hanya bisa kamu ambil.",
      s1Heading: "1. Pilih VOD yang boleh kamu potong",
      s1Body:
        "Pakai stream milikmu sendiri atau VOD yang ditentukan kampanye. Tunggu sampai siaran selesai: SupoClip bekerja pada video rekaman, bukan livestream yang sedang berjalan. Tempel tautan VOD atau klip Twitch maupun Kick, tautan YouTube, atau unggah filenya. Paket hosted dihitung berdasarkan menit video sumber, jadi VOD empat jam memakai 240 menit, entah kamu ambil tiga klip atau sepuluh.",
      s2Heading: "2. Biarkan SupoClip membuat daftar saran, lalu cari momen yang tak terdengar",
      s2Body:
        "SupoClip mentranskripsi stream dan mengusulkan momen lengkap dengan skor hook, keterlibatan, nilai, dan daya bagi. Gunakan skor ini untuk menentukan urutan tonton, bukan ramalan jumlah tayangan. Karena daftar saran bersumber dari ucapan, hasilnya kuat untuk kejar-kejaran bernarasi, reaksi, opini, dan jawaban ke chat, tetapi tidak melihat momen nyaris celaka yang hening.",
      s3Heading: "3. Atur framing streamer dan gameplay",
      s3BodyBefore:
        "Sebagian besar stream GTA menaruh webcam di sudut layar. Jika di-crop asal ke 9:16, hasilnya hanya wajah raksasa atau potongan kecil dari game. SupoClip mendeteksi webcam sudut yang stabil lalu menumpuknya di atas gameplay:",
      s3BodyAfter:
        "Tetap tonton seluruh klip dalam ukuran layar ponsel. Pastikan minimap atau mobil yang dikejar tidak terpotong, dan subtitle tidak menutupi aksi utama. Jika momennya murni gameplay, framing yang lebih lebar lebih enak ditonton daripada wajah besar.",
      s4Heading: "4. Potong untuk penonton baru",
      s4Body:
        "Penonton di feed tidak menyaksikan tiga jam sebelumnya. Mulai langsung di situasinya, pertahankan hanya konteks yang dibutuhkan, dan akhiri tepat setelah klimaksnya. Buka klip di studio untuk memangkas atau memisah, atau ketik perintah di kolom edit AI — misalnya “potong bagian hening sebelum kejar-kejaran” — lalu cek hasilnya sebelum disimpan.",
      s5Heading: "5. Perbaiki subtitle dan caption",
      s5Body:
        "Subtitle sudah sinkron per kata, tetapi transkripsi otomatis belum mengenal separuh nama di GTA 6. Perbaiki nama karakter dan lokasi (Lucia, Jason, Leonida, Vice City), username, serta teriakan. Setiap klip juga mendapat judul hook singkat di bagian atas pada detik-detik awal; pastikan isinya sesuai kejadian nyata, karena janji yang meleset bikin penonton pergi dan klip ditolak kampanye.",
      editorAlt: "Editor subtitle SupoClip dengan kontrol gaya, transkrip per kata, dan pratinjau area aman",
      editorCaption: "Editor SupoClip dengan video contoh: transkrip, gaya subtitle, dan pratinjau berdampingan.",
      s6Heading: "6. Ekspor, publikasikan, dan kirim tautan",
      s6Body:
        "Ekspor dalam 9:16 untuk TikTok, Shorts, atau Reels, atau publikasikan langsung ke akun TikTok dan YouTube yang terhubung dari SupoClip. Di paket Creator kamu bisa menjadwalkan unggahan lebih awal agar ritme tetap stabil sepanjang minggu rilis. Untuk kampanye, kirim tiap tautan segera setelah tayang dan buat catatan sederhana: VOD sumber, stempel waktu, jenis momen, dan tayangan.",
      scheduleLinkLabel: "Cara membuat dan menjadwalkan seminggu YouTube Shorts dari satu rekaman",
    },
    launchWeek: {
      heading: "Rencana untuk minggu peluncuran",
      lead: "Hari-hari pertama setelah rilis adalah masa paling ramai sekaligus paling banyak ditonton. Kecepatan itu penting, tetapi klip kasar yang diunggah buru-buru tetap kalah oleh klip rapi yang terbit satu jam kemudian. Ritme yang realistis:",
    },
    mistakes: {
      heading: "Kesalahan yang bikin clipper GTA 6 kehilangan tayangan",
      items: [
        {
          label: "Spoiler demi jangkauan.",
          body: "Memang cepat naik, tetapi lalu dilaporkan, dihapus, dan bisa menyeret seluruh akun.",
        },
        {
          label: "Klip yang butuh konteks stream penuh.",
          body: "“Akhirnya dia berhasil” tidak berarti apa-apa bagi penonton baru. Jelaskan berhasil melakukan apa.",
        },
        {
          label: "Berhenti sebelum klimaks.",
          body: "Kalau kamu meminta penonton menunggu sesuatu, tunjukkan hasilnya.",
        },
        {
          label: "Framing sulit dibaca.",
          body: "Teks HUD terlalu kecil dan crop yang membuang mobil dari layar. Selalu cek di ukuran ponsel.",
        },
        {
          label: "Musik berlisensi.",
          body: "Lagu radio di latar klip bisa membuat audio dibisukan atau pendapatannya diklaim pihak lain.",
        },
        {
          label: "Mengejar jumlah daripada kualitas.",
          body: "Dua puluh klip mirip dari satu kejar-kejaran hanya saling bersaing. Posting satu yang terbaik.",
        },
      ],
      moreBeforeFirst: "Pelajari lebih lanjut cara memilih momen yang kuat berdiri sendiri di ",
      moreFirstLink: "cara menemukan momen terbaik di video panjang",
      moreBetween: ", serta pengolahan rekaman stream secara umum di ",
      moreSecondLink: "cara mengubah VOD Twitch jadi klip TikTok dan Shorts",
      moreAfter: ".",
    },
    sources: {
      heading: "Sumber",
      gtaSix: "Rockstar Games: Grand Theft Auto VI",
      rockstarPolicy: "Rockstar Games: Kebijakan penayangan materi berhak cipta Rockstar Games",
      whopContentRewards: "Whop: Content Rewards",
      youtubeMonetization: "Bantuan YouTube: Kebijakan monetisasi channel",
      checkedNote:
        "Diperiksa 17 November 2026. Tarif kampanye dan aturan platform bisa berubah; cek versi terbaru sebelum mengandalkannya.",
    },
    calculator: {
      eyebrow: "Simulasi interaktif",
      title: "Perkiraan hasil sebulan membuat klip GTA 6",
      description:
        "Geser slider sesuai asumsimu sendiri. Bayaran per klip adalah tayangan yang disetujui × tarif kampanye, dibatasi oleh plafon per klip.",
      clipsPerWeekLabel: "Klip diposting per minggu",
      clipsPerWeekHint: "Hitung hanya klip yang benar-benar layak dan bangga kamu unggah.",
      viewsPerClipLabel: "Tayangan disetujui per klip (rata-rata)",
      viewsPerClipHint: "Kampanye membayar tayangan yang lolos peninjauan dalam jendela pelacakan mereka.",
      rateLabel: "Tarif per 1.000 tayangan",
      rateHint: "Ditetapkan masing-masing kampanye. Sekitar $1 adalah patokan yang umum.",
      capLabel: "Batas maksimal per klip",
      capNone: "Tanpa batas",
      capHint: "Banyak kampanye berhenti membayar satu klip setelah melewati angka tertentu. 0 berarti tanpa batas.",
      vodHoursLabel: "Jam stream yang kamu potong per bulan",
      vodHoursUnit: "jam",
      vodHoursHint: "SupoClip hosted menghitung menit video sumber, bukan jumlah klip.",
      payoutLabel: "Perkiraan bayaran",
      payoutDetail: (clipsPerMonth, formattedViews) => `${clipsPerMonth} klip · ${formattedViews} tayangan/bulan`,
      perClipLabel: "Per klip",
      cappedDetail: "Setiap klip menyentuh batas maksimal di jumlah tayangan ini.",
      uncappedDetail: "Masih di bawah batas maksimal per klip.",
      afterPlanLabel: "Setelah paket SupoClip",
      planFitsDetail: (minutes, planName, formattedPrice, planMinutes) =>
        `${minutes} menit sumber masuk paket ${planName} (${formattedPrice}/bln, ${planMinutes} mnt).`,
      planOverDetail: (minutes) =>
        `${minutes} menit melebihi batas 1.500 menit Creator. Kurangi jam stream, atau host sendiri.`,
      averageLead: "Angka rata-rata menyembunyikan distribusi aslinya.",
      averageBody:
        " Satu klip dengan 200.000 tayangan dan sembilan belas klip dengan 500 tayangan punya rata-rata sekitar 10.500 tayangan per klip, tetapi dengan batas $150 per klip bulan itu hanya menghasilkan sekitar $160, bukan $210 seperti hitungan merata. Bersiaplah sebagian besar klip menghasilkan sedikit dan segelintir klip menopang sisanya.",
      disclaimer:
        "Hanya perkiraan, sebelum pajak dan biaya. Ini bukan ramalan pendapatan, dan anggaran kampanye bisa habis sebelum tayanganmu dihitung.",
    },
    workflowDiagram: {
      caption: "Alur clipping GTA 6 · kamu yang memutuskan, SupoClip mengerjakan bagian berulang",
      youLabel: "Kamu",
      appLabel: "SupoClip",
      steps: [
        { title: "Pilih VOD", you: "Stream milikmu atau yang diizinkan kampanye", app: "Impor tautan Twitch, Kick, YouTube, atau file" },
        { title: "Daftar saran", you: "Tonton kandidat, catat aksi tanpa suara", app: "Transkripsi ucapan dan urutkan momen lisan" },
        { title: "Framing", you: "Cek tiap pergantian adegan di ukuran ponsel", app: "Menumpuk webcam sudut di atas gameplay" },
        { title: "Potong", you: "Hook di detik awal, tutup di klimaksnya", app: "Pangkas, pisah, dan edit AI di studio" },
        { title: "Subtitle", you: "Perbaiki nama: Lucia, Jason, Leonida, Vice City", app: "Subtitle sinkron per kata dan judul hook" },
        { title: "Publikasi", you: "Kirim tautan ke kampanye dan catat hasilnya", app: "Ekspor 9:16, posting ke TikTok dan YouTube" },
      ],
    },
    reactionLayout: {
      svgTitle: "Dari stream 16:9 menjadi klip vertikal 9:16 bertumpuk",
      svgDesc:
        "Di kiri, stream layar lebar dengan webcam di sudut kanan atas. Di kanan, klip vertikal: webcam mengisi sepertiga atas dan gameplay mengisi dua pertiga bawah.",
      sourceTitle: "VOD Stream · 16:9",
      sourceSubtitle: "Webcam tetap di sudut atas",
      captionsHere: "subtitle tampil di sini",
      outputTitle: "Klip · 9:16 · 1080×1920",
      figcaption:
        "Saat SupoClip menemukan wajah stabil di sudut atas layar, streamer ditempatkan di sepertiga atas dan gameplay di bawahnya. Tidak ada webcam tetap, atau posisi webcam berpindah? SupoClip beralih ke tata letak lain, jadi selalu cek hasilnya.",
    },
    clipAnatomy: {
      eyebrow: "Anatomi satu klip",
      title: "Kejar-kejaran GTA 6 berdurasi 43 detik yang dipotong agar penonton betah",
      hookBarNote: "Judul hook tampil di layar selama ~4 dtk pertama",
      secondsUnit: "dtk",
      parts: [
        { label: "Hook", from: 0, to: 3, note: "Sebut atau tunjukkan situasinya: “bintang enam baru semenit main”." },
        { label: "Konteks", from: 3, to: 10, note: "Hanya informasi yang dibutuhkan penonton baru agar nyambung." },
        { label: "Bangunan", from: 10, to: 32, note: "Kejar-kejaran, perampokan, atau debat dengan chat." },
        { label: "Klimaks", from: 32, to: 40, note: "Tabrakan, pelarian, atau reaksinya." },
        { label: "Tutup", from: 40, to: 43, note: "Berhenti di sini. Tanpa “ya udah, jadi…”" },
      ],
    },
    rulesBoard: {
      caption: "Apa yang boleh diposting · ringkasan praktis, bukan nasihat hukum",
      groups: [
        {
          status: "ok",
          label: "Umumnya aman",
          items: [
            "Gameplay milikmu sendiri dengan komentarmu",
            "Momen cerita di dalam playthrough yang dinarasikan",
            "Stream yang pemiliknya membuka kampanye clipping, sesuai aturannya",
          ],
        },
        {
          status: "warn",
          label: "Cek dulu",
          items: [
            "Monetisasi: kebijakan Rockstar Games ditulis untuk penggunaan nonkomersial",
            "Lagu radio berlisensi: bisa dibisukan atau terkena klaim",
            "Stream orang lain tanpa kampanye atau izin",
          ],
        },
        {
          status: "no",
          label: "Rawan dihapus",
          items: [
            "Akhir cerita atau spoiler plot besar",
            "Cutscene terpisah dan kompilasi sinematik",
            "Bocoran, cheat, dan mod tanpa izin",
          ],
        },
      ],
    },
    launchWeekTable: {
      caption: "Rencana minggu peluncuran untuk klip GTA 6 (rilis: 19 November 2026)",
      headers: ["Kapan", "Fokus", "Yang dilakukan"],
      rows: [
        [
          "Sebelum 19 Nov",
          "Persiapan",
          "Hubungkan TikTok dan YouTube, pilih gaya subtitle, baca aturan kampanye yang ingin diikuti. Rekaman sebelum rilis akan dihapus, jadi jangan posting apa pun.",
        ],
        [
          "19 Nov (rilis)",
          "Stream atau tonton",
          "Mainkan atau ikuti stream yang boleh kamu potong. Catat stempel waktu momen tanpa suara saat kejadian berlangsung.",
        ],
        [
          "20 Nov",
          "Batch pertama",
          "Impor VOD kemarin. Ambil 3–5 momen lisan terkuat; selesaikan dengan rapi alih-alih mengunggah dua puluh klip kasar.",
        ],
        [
          "21–23 Nov",
          "Ritme stabil",
          "Satu VOD masuk, beberapa klip keluar setiap hari. Hindari alur utama: klip spoiler cepat dilaporkan dan dihapus.",
        ],
        [
          "24–25 Nov",
          "Baca datanya",
          "Bandingkan jenis momen yang paling menahan penonton: kejar-kejaran, reaksi, temuan peta, atau opini. Perbanyak tipe yang paling berhasil.",
        ],
      ],
    },
    momentMap: {
      eyebrow: "Peta momen · contoh stream",
      title: "Satu stream GTA 6 empat jam, tiga jenis momen",
      description:
        "Pilih salah satu penanda. SupoClip membaca transkrip, jadi momen lisan masuk daftar saran; aksi hening perlu matamu sendiri, dan spoiler sebaiknya ditinggal.",
      legendAria: "Legenda",
      momentsAria: "Momen di dalam stream",
      kinds: {
        spoken: { label: "Momen lisan: daftar saran AI bisa menemukannya", short: "AI temukan" },
        silent: { label: "Aksi hening: cek sendiri", short: "Cek manual" },
        skip: { label: "Spoiler cerita atau cutscene: lewatkan", short: "Lewati" },
      },
      moments: [
        { minute: 6, kind: "spoken", title: "Reaksi pertama terhadap pemandangan pembuka", note: "Streamer menjelaskan apa yang ia lihat dan kenapa kaget. Ucapan membawa momennya, jadi transkrip mudah menangkapnya." },
        { minute: 31, kind: "skip", title: "Cutscene cerita", note: "Cutscene yang dipotong terpisah termasuk hal yang dihapus oleh kebijakan video Rockstar Games. Simpan momen cerita dalam narasi atau lewatkan." },
        { minute: 58, kind: "silent", title: "Nyaris tabrakan di jalan tol", note: "Aksi keren tanpa sepatah kata pun. Daftar berbasis ucapan bisa melewatkannya; catat menitnya saat menonton." },
        { minute: 84, kind: "spoken", title: "Chat bertanya soal ukuran peta", note: "Pertanyaan plus jawaban yang jelas. Tampilkan pertanyaannya di layar atau di kalimat pertama." },
        { minute: 112, kind: "spoken", title: "Kejar-kejaran bintang lima sambil dikomentari", note: "Aksi dengan komentar langsung. Gabungkan awal kejadian, kejar-kejaran, dan reaksinya dalam satu klip." },
        { minute: 139, kind: "silent", title: "Bug fisika", note: "Lucu ditonton, tapi tidak ada yang menjelaskannya. Cari dengan menggeser timeline lalu fokuskan bingkai pada aksinya, bukan wajah." },
        { minute: 171, kind: "spoken", title: "Opini tajam soal soundtrack", note: "Pendapat lengkap dengan alasannya. Bisukan atau ganti musik radio berlisensi sebelum posting." },
        { minute: 203, kind: "skip", title: "Bocoran akhir cerita", note: "Memposting ending atau twist besar adalah sasaran utama kebijakan, dan pasti dilaporkan." },
        { minute: 226, kind: "spoken", title: "Penutup: momen terbaik hari ini", note: "Streamer merangkum highlight-nya sendiri. Berguna sebagai ide judul hook sekaligus penunjuk ke momen aslinya." },
      ],
      showTableSummary: "Tampilkan semua momen sebagai tabel",
      tableHeaders: ["Waktu", "Momen", "Tinjauan"],
    },
  },
  creatorGuides: {
    "twitch-vod-to-shorts": {
      post: {
        title: "Cara Mengubah VOD Twitch Menjadi Klip TikTok dan Shorts",
        description:
          "Ubah VOD Twitch jadi klip vertikal: impor siaran selesai, temukan highlight lisan, cek framing gameplay, tambahkan subtitle, dan ekspor untuk TikTok atau Shorts.",
        eyebrow: "Klip stream",
        category: "Panduan",
        readingTime: "6 mnt baca",
        summary:
          "Ubah VOD Twitch jadi klip vertikal: impor siaran selesai, temukan highlight lisan, cek framing gameplay, tambahkan subtitle, dan ekspor untuk TikTok atau Shorts.",
        keywords: ["VOD Twitch ke Shorts", "VOD Twitch ke TikTok", "pembuat klip Twitch", "AI stream clipper"],
      },
      intro:
        "Stream panjang berisi berbagai jenis momen: cerita, reaksi, penjelasan, dan aksi yang terjadi tanpa sepatah kata. Semuanya butuh keputusan edit yang berbeda. Pakai daftar saran AI untuk momen lisan, lalu tinjau gameplay dan framing-nya sendiri sebelum mengubah VOD Twitch menjadi klip vertikal.",
      sections: [
        {
          id: "import",
          heading: "1. Impor siaran yang sudah selesai",
          paragraphs: [
            "Tunggu sampai stream berakhir lalu pakai tautan VOD atau klip Twitch, atau unggah rekamanmu sendiri. SupoClip menerima sumber-sumber itu; SupoClip tidak memotong livestream yang masih berlangsung. Simpan salinan asli dari rekaman yang berencana kamu olah ulang.",
            "Jika VOD tidak tersedia, dibatasi, atau tidak bisa diunduh, gunakan rekaman lokal yang kamu punya izin untuk mengeditnya. Mulai dari rekaman berdurasi wajar daripada mengirim seluruh arsip sekaligus. Periksa batas unggahan dan durasi yang ditampilkan aplikasi untuk paket atau instalasimu.",
          ],
        },
        {
          id: "moments",
          heading: "2. Pisahkan highlight lisan dari aksi hening",
          paragraphs: [
            "Kirim rekaman untuk dianalisis lalu tinjau momen yang diusulkan. Pemilihan berbasis transkrip sangat cocok untuk cerita di sela pertandingan, penjelasan strategi, atau reaksi yang komentarnya menjelaskan apa yang terjadi.",
            "Aksi clutch yang hening atau kejadian visual halus di layar bisa saja tidak pernah muncul di transkrip. Gunakan stempel waktu dari catatanmu sendiri untuk mengecek bagian itu di rekaman asli. Jangan anggap ketiadaan kandidat AI berarti tidak ada momen menarik.",
          ],
          table: {
            caption: "Jenis tinjauan yang dibutuhkan setiap momen stream",
            headers: ["Momen", "Yang dicari", "Periksa sebelum ekspor"],
            rows: [
              ["Cerita atau opini", "Pembuka yang jelas dan penutup yang utuh", "Hapus referensi ke chat sebelumnya yang tidak bisa dilihat penonton"],
              ["Reaksi lisan", "Awalan yang cukup agar penonton paham penyebab reaksinya", "Satukan kejadian dan reaksinya dalam satu potongan"],
              ["Gameplay hening", "Aksi sesungguhnya di rekaman sumber", "Pilih manual; analisis ucapan bisa melewatkannya"],
              ["Respons ke chat", "Pertanyaan ditambah jawaban yang jelas", "Jaga konteks tanpa membocorkan informasi pribadi"],
            ],
          },
          links: [
            { href: "/blog/find-best-moments-in-video", label: "Periksa apakah sebuah highlight kuat berdiri sendiri" },
            { href: "/blog/how-to-clip-gta-6", label: "Memotong stream GTA 6? Ikuti alur lengkapnya" },
          ],
        },
        {
          id: "frame",
          heading: "3. Jaga aksi penting tetap di dalam bingkai vertikal",
          paragraphs: [
            "Crop fokus wajah pas dipakai saat kamu berbicara langsung ke kamera. Namun itu bisa keliru ketika penonton perlu melihat minimap, papan skor, inventaris, atau sudut lebar permainan. Tonton seluruh kandidat dalam rasio tujuan dan sesuaikan framing-nya di editor.",
            "Jika aksi tidak terbaca saat di-crop terlalu ketat, gunakan framing yang menyisakan lebih banyak gambar asli daripada memaksa setiap momen jadi close-up. Alur ini tidak menjanjikan tata letak tumpuk webcam-dan-gameplay otomatis di semua kondisi. Tampilan lebar yang terbaca jelas lebih baik bagi klip dibanding wajah besar yang menutupi kejadiannya.",
          ],
          bullets: [
            "Cek bagian awal, momen aksinya, dan setiap perpindahan kamera atau adegan.",
            "Pastikan subtitle tidak menutupi UI game atau target penting.",
            "Pratinjau di layar ponsel; teks antarmuka yang kecil bisa tidak terbaca meski tetap masuk bingkai.",
          ],
        },
        {
          id: "captions",
          heading: "4. Perbaiki nama dalam game dan rapikan penutup",
          paragraphs: [
            "Subtitle otomatis bisa salah menangkap username, nama karakter, singkatan, dan kata yang diteriakkan. Perbaiki di transkrip lalu putar ulang audionya. Pertahankan bahasa gaul yang memang disengaja untuk menjaga makna, tetapi jangan biarkan salah ketik transkripsi hanya karena terdengar mirip.",
            "Potong setelah kejadian atau penjelasan tuntas. Jika klip berakhir dengan “tunggu sampai kamu lihat apa yang terjadi”, sertakan kejadian yang ditunggu itu. Hook yang baik menggambarkan situasi sebenarnya, bukan janji yang tidak ada di videonya.",
          ],
          links: [{ href: "/blog/word-by-word-video-captions", label: "Buat subtitle mudah dibaca tanpa menutupi aksi" }],
        },
        {
          id: "export",
          heading: "5. Ekspor untuk TikTok atau YouTube Shorts",
          paragraphs: [
            "Pilih preset ekspor platform lalu unduh video hasil render. Unggah versi TikTok secara mandiri atau lewat koneksi akun yang tersedia. Untuk YouTube, kamu juga bisa memakai fitur publikasi SupoClip bila aktif di akunmu; penjadwalan membutuhkan paket dan akses yang sesuai.",
            "Buat catatan sederhana berisi VOD sumber, stempel waktu, topik, dan tujuan unggah. Bandingkan hasil cerita, reaksi, dan penjelasan secara terpisah. Dengan begitu kamu tahu jenis momen stream apa yang paling disukai audiensmu alih-alih memperlakukan semua klip sebagai eksperimen yang sama.",
          ],
        },
      ],
      faqs: [
        {
          question: "Bisakah SupoClip memotong stream Twitch saat saya sedang live?",
          answer:
            "Tidak. Gunakan VOD atau klip setelah siaran berakhir, atau unggah rekamanmu. Alur yang dijelaskan di sini ditujukan untuk video rekaman.",
        },
        {
          question: "Apakah AI akan mendeteksi setiap highlight gaming?",
          answer:
            "Tidak. SupoClip memakai transkrip untuk memilih momen, jadi gameplay hening yang penting bisa terlewat. Tinjau momen visual secara manual dan periksa crop akhirnya.",
        },
        {
          question: "Apakah panduan ini langsung memposting klip ke TikTok?",
          answer:
            "Panduan ini memakai hasil ekspor TikTok yang diunduh. Jangan berasumsi publikasi langsung ke TikTok tersedia di semua akun; cek platform terhubung dan akses yang tampil di akunmu.",
        },
      ],
      cta: {
        title: "Beri kehidupan kedua untuk VOD berikutnya",
        body: "Impor stream yang sudah selesai, tinjau highlight lisan, lalu ekspor momen yang kuat di luar siaran langsung.",
        label: "Potong VOD Twitch saya",
      },
      gallery: {
        heading: "Apa yang bisa ditunjukkan klip berbasis ucapan",
        intro:
          "Sumber ini adalah wawancara rekaman, bukan siaran Twitch atau demo deteksi gameplay. Gunakan untuk meninjau alur jawaban lisan yang dijelaskan di atas; aksi hening atau tata letak webcam di atas game tetap memerlukan peninjauan visual tersendiri.",
        notes: {
          "ton-2":
            "Di sini kata-katalah yang membawa gagasannya, sehingga cocok sebagai contoh seleksi berbasis transkrip. Pada stream, terapkan uji konteks yang sama pada cerita atau penjelasan, lalu cek terpisah bahwa aksi game tetap terlihat.",
        },
      },
    },
    "podcast-to-shorts": {
      post: {
        title: "Cara Mengubah Video Podcast Menjadi Shorts dan Reels",
        description:
          "Ubah video podcast menjadi Shorts dan Reels ber-subtitle: pilih momen yang utuh, perbaiki framing pembicara, tinjau subtitle, dan ekspor klip dengan SupoClip.",
        eyebrow: "Alur kerja kreator",
        category: "Panduan",
        readingTime: "7 mnt baca",
        summary:
          "Ubah video podcast menjadi Shorts dan Reels ber-subtitle: pilih momen yang utuh, perbaiki framing pembicara, tinjau subtitle, dan ekspor klip dengan SupoClip.",
        keywords: ["podcast ke Shorts", "klip podcast", "podcast ke Reels", "video podcast clipper"],
        imageAlt: "Ton Roosendaal dalam wawancara sumber yang digunakan untuk contoh klip percakapan SupoClip",
      },
      intro:
        "Klip podcast yang berguna memberi penonton baru satu gagasan utuh tanpa memaksa mereka menonton episode penuh terlebih dahulu. Mulai dengan pertanyaan, pengamatan mengejutkan, atau pelajaran spesifik; pertahankan penjelasan yang mengantarkan poinnya. Ini cara berulang untuk melakukannya pada video podcast, dari rekaman asli sampai ekspor vertikal.",
      sections: [
        {
          id: "prepare",
          heading: "1. Mulai dari rekaman yang bisa diolah ulang",
          paragraphs: [
            "Gunakan versi video dari episodenya: tautan YouTube atau file video yang bisa kamu unggah. Track suara yang jernih jauh lebih penting daripada set studio yang mewah. Jika audionya keruh, perbaiki dulu sebelum meminta sistem transkripsi mencari momen penting.",
            "Pilih episode yang punya jawaban atau cerita yang tegas. Percakapan tentang satu kesalahan nyata, keputusan, atau hasil tertentu lebih mudah dijadikan klip mandiri dibanding pembukaan panjang. Pastikan kamu punya izin mengolah ulang rekaman dan materi narasumber. Alur ini mengasumsikan rekaman video; episode audio-saja butuh pengolahan visual dulu.",
          ],
          bullets: [
            "Simpan judul episode, nama narasumber, dan tautan sumber di catatan proyekmu.",
            "Cek durasi video sumber terhadap batas paket hosted sebelum mengirim episode panjang.",
            "Jika tautan sumber tidak bisa diimpor, unggah langsung file video aslimu.",
          ],
        },
        {
          id: "select",
          heading: "2. Buat kandidat, lalu pilih gagasan yang utuh",
          paragraphs: [
            "Tempel tautan episode ke SupoClip atau unggah filenya. Aplikasi akan mentranskripsi ucapan dan mengusulkan segmen pendek. Perlakukan skornya sebagai urutan tinjauan: skor tinggi menunjukkan kandidat mana yang dicek duluan, bukan berapa banyak tayangan yang akan didapat.",
            "Putar tiap kandidat tanpa bagian percakapan lainnya. Apakah topiknya langsung jelas di kalimat pembuka? Apakah jawabannya memberi sesuatu yang konkret? Klip yang dibuka dengan “nah itu masalahnya” perlu kalimat sebelumnya atau titik mulai lain. Pangkas basa-basi dan pembukaan berulang, tetapi pertahankan syarat atau batasan yang menentukan makna ucapan pembicara.",
          ],
          links: [{ href: "/blog/find-best-moments-in-video", label: "Gunakan daftar cek pemilihan highlight" }],
        },
        {
          id: "example",
          heading: "Contoh nyata: memotong wawancara rekaman",
          paragraphs: [
            "Wawancara rekaman punya tantangan konteks yang sama dengan video podcast: sebuah jawaban bisa bergantung pada pertanyaan atau obrolan sebelumnya. Untuk contoh di bawah, kami menjalankan wawancara Creative Commons bersama pendiri Blender Ton Roosendaal melalui SupoClip dan merender cuplikan utuh ber-subtitle.",
            "Gunakan tautan sumber untuk membandingkan tiap potongan dengan aslinya. Dengarkan topiknya, ikuti penjelasannya, dan cek bagian apa yang ditinggalkan di luar cuplikan. Ini contoh alur kerja clipping, bukan klaim bahwa pembicara memakai atau merekomendasikan SupoClip.",
          ],
        },
        {
          id: "edit",
          heading: "3. Tinjau framing pembicara dan subtitle bersamaan",
          paragraphs: [
            "Buka klip pilihan di editor. Gunakan framing vertikal untuk Shorts dan Reels, lalu tonton seluruh potongannya. Crop yang pas di host bisa meleset dari tamu saat kamera berpindah. Jika grafik atau komposisi dua orang itu penting, sisakan cukup bagian dari bingkai asli agar tetap masuk akal.",
            "Perbaiki nama, ejaan merek, angka, dan tanda baca di transkrip. Jauhkan subtitle dari wajah dan tombol antarmuka platform. Pratinjau dalam ukuran ponsel, jangan hanya menilai keterbacaan di monitor desktop. Jika gaya caption malah mengalihkan perhatian dari jawaban, sederhanakan animasinya dan sorot lebih sedikit kata.",
          ],
          links: [{ href: "/blog/word-by-word-video-captions", label: "Atur gaya dan koreksi subtitle kata per kata" }],
        },
        {
          id: "publish",
          heading: "4. Ekspor tiap klip dengan alasan jelas untuk ditonton",
          paragraphs: [
            "Ekspor memakai preset tujuanmu. Beri tiap klip judul yang menjelaskan inti sebenarnya. Hindari hook dramatis yang tidak pernah dibuktikan narasumber: penonton harus menemukan jawaban yang dijanjikan di dalam klip.",
            "Unggah file ekspor ke Reels, atau publikasikan ke YouTube lewat SupoClip bila fitur publikasi aktif di akunmu. Penjadwalan bergantung pada paket dan akses publikasi di instalasimu. Taruh tautan episode penuh di deskripsi atau profil agar penonton yang tertarik bisa lanjut menonton.",
          ],
          bullets: [
            "Tonton file hasil ekspor secara penuh, termasuk detik pertama dan terakhir.",
            "Pastikan teks penting tidak tertutup elemen overlay aplikasi tujuan.",
            "Catat dari episode dan momen sumber mana setiap klip berasal.",
            "Evaluasi data tayangan dan respons nyata sebelum menentukan topik berikutnya untuk dipotong.",
          ],
          links: [{ href: "/blog/schedule-week-of-youtube-shorts", label: "Susun seminggu Shorts dari satu rekaman" }],
        },
      ],
      faqs: [
        {
          question: "Bisakah saya membuat Reels dari podcast di YouTube?",
          answer:
            "Bisa. Selama kamu punya izin untuk mengolahnya ulang, pakai video YouTube sebagai sumber, buat klip vertikal, lalu unduh ekspor Reels. Unggah file tersebut ke Instagram; panduan ini tidak mengasumsikan publikasi langsung ke Instagram sudah tersedia.",
        },
        {
          question: "Berapa banyak klip yang sebaiknya dibuat dari satu episode?",
          answer:
            "Ambil sebanyak gagasan utuh dan berbeda yang memang layak tayang. Tidak ada angka baku per episode, dan mengulang jawaban yang sama dengan hook berbeda tidak menghasilkan poin baru.",
        },
        {
          question: "Bolehkah klip pendek membuang pertanyaan pewawancara?",
          answer:
            "Boleh, jika jawabannya sudah menyebut topiknya dan langsung paham saat berdiri sendiri. Jika tidak, pertahankan versi singkat pertanyaannya atau mulai lebih awal agar konteks tetap terjaga.",
        },
      ],
      cta: {
        title: "Ubah episode podcast berikutnya jadi kumpulan klip",
        body: "Bawa satu episode video. Tinjau momen yang disarankan, perbaiki subtitle-nya, dan ekspor klip yang benar-benar ingin kamu terbitkan. SupoClip hosted berbayar; host sendiri adalah opsi lain.",
        label: "Potong podcast saya",
      },
      gallery: {
        heading: "Dari percakapan rekaman menjadi jawaban yang utuh",
        intro:
          "Contoh ini menggunakan wawancara dengan pendiri Blender Ton Roosendaal. Memang ini wawancara dan bukan episode podcast, tetapi uji editorialnya sama: apakah jawabannya memperkenalkan topik dan memberi gagasan berguna tanpa percakapan di sekelilingnya?",
        notes: {
          "ton-1":
            "Dengarkan topiknya sejak kalimat pembuka, lalu ikuti penjelasannya sampai akhir. Bandingkan potongan dengan sumber untuk memastikan maksud pembicara tetap utuh dalam format pendek.",
          "ton-2":
            "Tinjau jawaban kedua sebagai unggahan terpisah, bukan lanjutan. Subtitle dan framing harus membantu penonton baru memahaminya tanpa harus melihat klip pertama.",
        },
      },
    },
    "find-best-moments-in-video": {
      post: {
        title: "Cara Menemukan Momen Terbaik di Video Panjang",
        description:
          "Pilih highlight video yang lebih kuat dengan daftar cek praktis untuk hook, konteks, klimaks, dan potongan rapi. Manfaatkan saran AI tanpa bergantung pada skor viralitas.",
        eyebrow: "Alur kerja kreator",
        category: "Panduan",
        readingTime: "6 mnt baca",
        summary:
          "Pilih highlight video yang lebih kuat dengan daftar cek praktis untuk hook, konteks, klimaks, dan potongan rapi. Manfaatkan saran AI tanpa bergantung pada skor viralitas.",
        keywords: ["temukan momen terbaik di video", "cari highlight video", "deteksi highlight AI", "pilih klip podcast"],
      },
      intro:
        "Momen terbaik dalam rekaman tidak selalu yang paling keras, paling lucu, atau paling emosional. Untuk video pendek, momen itu juga harus tetap masuk akal di luar konteks aslinya. Gunakan panduan cek di bawah untuk menyaring rekaman panjang menjadi daftar pendek momen yang langsung dipahami penonton baru.",
      sections: [
        {
          id: "brief",
          heading: "Tentukan siapa yang ingin dibantu oleh klip ini",
          paragraphs: [
            "Tulis satu kalimat sebelum mulai mencari: misalnya, “Klip ini harus membantu founder pemula menghadapi obrolan sulit dengan pelanggan.” Audiens yang sempit memberi aturan seleksi yang jelas. Tanpa itu, kalimat mengejutkan apa pun bisa terlihat seperti highlight bagus.",
            "Telusuri bab, pertanyaan, dan pergantian topik untuk mencari jawaban atas brief tersebut. Kandidat yang kuat sering memuat kesalahan konkret, penjelasan sebelum-sesudah, demonstrasi, atau perbedaan pendapat dengan alasan jelas. Tandai perkiraan stempel waktu; batas potongannya akan kamu rapikan nanti.",
          ],
        },
        {
          id: "checklist",
          heading: "Gunakan empat cek sebelum menyimpan sebuah momen",
          paragraphs: [
            "Tonton kandidat seolah-olah ia muncul di feed tanpa judul episode. Tiap cek menjawab pertanyaan yang berbeda. Pembukaan menarik tidak bisa menambal penjelasan yang hilang, dan penjelasan yang lengkap tetap bisa butuh kalimat pembuka yang lebih bersih.",
          ],
          table: {
            caption: "Daftar cek editorial untuk highlight video yang berdiri sendiri",
            headers: ["Cek", "Pertahankan jika", "Perbaiki jika"],
            rows: [
              ["Hook", "Pembuka menyebut masalah, ketegangan, atau hasil spesifik", "Dimulai dengan sapaan atau kata “ini” dan “itu” tanpa konteks"],
              ["Konteks", "Topik dan syarat pentingnya langsung jelas", "Butuh lima menit sebelumnya untuk memahaminya"],
              ["Klimaks", "Pembicara memberi alasan, contoh, atau jawaban berguna", "Klip memberi janji tetapi terpotong sebelum jawabannya"],
              ["Batas potong", "Klip mulai dan berakhir pada pikiran yang utuh", "Potongan mengubah makna atau memutus kesimpulan"],
            ],
          },
          bullets: [
            "Dengarkan sekali tanpa melihat gambar: apakah argumennya tetap runtut?",
            "Tonton sekali tanpa suara: apakah framing dan subtitle sanggup membawa topiknya?",
            "Periksa sumber di sekitar kedua titik potong agar tidak menghapus catatan penting yang memengaruhi makna.",
          ],
        },
        {
          id: "example",
          heading: "Bandingkan gagasan utuh dengan kutipan terpisah",
          paragraphs: [
            "Pernyataan umum tentang melatih keahlian bisa terdengar bagus sebagai kutipan tetapi membuang penjelasan yang berguna. Potongan yang lebih kuat menyertakan langkah yang disarankan pembicara dan cukup alasan di baliknya. Rekaman asli di sekitar kedua batas potong adalah acuan apakah versi pendeknya tetap akurat.",
            "Contoh Andrew Price di bawah berasal dari presentasi populernya yang sama. Terapkan daftar cek pada masing-masing: apakah cuplikan mempertahankan penjelasan yang membuat pembukaannya berguna? Jika kandidat butuh konteks lebih banyak daripada yang muat di video pendek, biarkan tetap di rekaman utuhnya.",
          ],
        },
        {
          id: "ai",
          heading: "Biarkan AI mempercepat pencarian, lalu kamu yang memutuskan",
          paragraphs: [
            "AI video clipper bisa mentranskripsi rekaman, mengusulkan segmen kandidat, dan mengurutkannya untuk ditinjau. Di SupoClip, skor hook, keterlibatan, nilai, dan daya bagi membantu menyusun daftar pendek tersebut. Itu adalah sinyal editorial, bukan ramalan jumlah tayangan.",
            "Analisis berbasis transkrip paling efektif saat momen menarik disampaikan lewat ucapan. Demo produk tanpa suara, kejutan visual, atau aksi penentu kemenangan bisa sangat berharga tanpa diceritakan keras-keras. Tinjau momen seperti itu secara manual alih-alih menganggap pencarian transkrip menemukan semuanya.",
          ],
          links: [{ href: "/ai-video-clipper", label: "Lihat apa saja yang diotomatisasi AI video clipper" }],
        },
        {
          id: "refine",
          heading: "Rapikan potongan sebelum menambahkan efek",
          paragraphs: [
            "Atur titik mulai tepat sebelum pikiran utuh pertama yang berguna. Buang pembukaan berulang bila memungkinkan, lalu putar penutupnya bersama kalimat sebelumnya. Berhenti setelah klimaks daripada menyeret topik berikutnya ke dalam klip. Pertahankan jeda yang membuat jawaban lebih mudah dicerna.",
            "Baru setelah itu sesuaikan subtitle, crop, dan teks hook. Simpan catatan singkat mengapa kamu memilih momen tersebut. Setelah tayang, bandingkan respons audiens dengan hipotesis awal tadi. Jika penonton pergi sebelum penjelasan, uji pembukaan yang lebih jelas; jika mereka menonton tanpa bereaksi, tinjau lagi apakah intinya benar-benar relevan bagi audiens yang kamu sasar.",
          ],
          links: [
            { href: "/blog/podcast-to-shorts", label: "Terapkan daftar cek ini pada video podcast" },
            { href: "/blog/repurpose-webinar-clips", label: "Terapkan pada rekaman webinar" },
          ],
        },
      ],
      faqs: [
        {
          question: "Apakah skor viralitas tinggi menjamin klip akan sukses?",
          answer:
            "Tidak. Skor membantu memprioritaskan urutan tinjauan, tetapi distribusi, kecocokan audiens, editan akhir, dan kualitas rekaman asli tetap menentukan. Nilai performa dari hasil publikasi sebenarnya.",
        },
        {
          question: "Apakah setiap highlight harus punya durasi yang sama?",
          answer:
            "Tidak. Pertahankan potongan paling ringkas yang tetap menyampaikan gagasan secara utuh dan akurat. Cek ketentuan format terbaru di masing-masing platform secara terpisah.",
        },
        {
          question: "Bagaimana jika momen terbaiknya sebagian besar visual?",
          answer:
            "Periksa secara manual. Pencarian berbasis transkrip bisa melewatkan kejadian yang tidak dijelaskan lewat ucapan. Pilih framing yang mempertahankan aksi visual daripada menganggap crop wajah selalu cocok.",
        },
      ],
      cta: {
        title: "Temukan daftar momen terbaik dari rekamanmu sendiri",
        body: "Gunakan SupoClip untuk mengusulkan momen, lalu terapkan cek hook, konteks, klimaks, dan batas potong sebelum ekspor.",
        label: "Temukan highlight video saya",
      },
      gallery: {
        heading: "Dua gagasan berbeda dari presentasi populer Andrew Price",
        intro:
          "Presentasi aslinya memiliki 3,8 juta tayangan YouTube saat dipilih pada 26 Oktober 2026. Ini adalah render baru dari SupoClip, bukan tangkapan layar Shorts milik orang lain. Putar contoh lengkapnya dan pakai tautan sumber untuk memeriksa batas potongnya.",
        notes: {
          "price-1":
            "Terapkan keempat cek: kenali gagasan pembuka, konteks yang membuatnya akurat, penjelasan yang mengantar klimaks, dan titik akhir cuplikannya.",
          "price-2":
            "Bandingkan gagasan ini dengan klip pertama. Menjaga pesan yang berbeda lebih penting daripada mengejar kuota dengan beberapa versi dari jawaban yang sama.",
        },
      },
    },
    "word-by-word-video-captions": {
      post: {
        title: "Cara Menambahkan Subtitle Kata per Kata pada Video Pendek",
        description:
          "Buat subtitle kata per kata yang mudah dibaca untuk Shorts, Reels, dan TikTok. Perbaiki transkrip, pilih gaya, cek area aman, dan ekspor dengan SupoClip.",
        eyebrow: "Alur kerja kreator",
        category: "Panduan",
        readingTime: "6 mnt baca",
        summary:
          "Buat subtitle kata per kata yang mudah dibaca untuk Shorts, Reels, dan TikTok. Perbaiki transkrip, pilih gaya, cek area aman, dan ekspor dengan SupoClip.",
        keywords: ["subtitle kata per kata", "caption animasi video", "tambah subtitle ke Shorts", "subtitle video otomatis"],
        imageAlt: "Editor SupoClip dengan gaya subtitle, waktu per kata, dan pratinjau video vertikal",
      },
      intro:
        "Subtitle kata per kata menyorot ucapan tepat saat kata diucapkan. Fitur ini baru benar-benar membantu bila kata-katanya akurat, mudah dibaca, dan diletakkan di tempat yang terlihat jelas. Rancang gayanya mengikuti rekamanmu daripada langsung memilih animasi yang paling ramai.",
      sections: [
        {
          id: "transcribe",
          heading: "1. Buat klip dan periksa transkripnya",
          paragraphs: [
            "Impor tautan video yang didukung atau unggah video ke SupoClip. Setelah klip selesai dibuat, buka di editor dan cek transkripnya sambil memutar video. Stempel waktu tingkat kata menghubungkan teks dengan audio ucapan.",
            "Mulai dari akurasi. Cek nama, angka, istilah produk, dan kata yang diucapkan di atas musik atau suara lain. Dengarkan frasa yang meragukan dalam konteks satu kalimat sebelum mengubahnya. Subtitle harus mencerminkan apa yang benar-benar diucapkan, bukan diam-diam mengubah pernyataan ragu menjadi klaim pasti.",
          ],
        },
        {
          id: "correct",
          heading: "2. Perbaiki kata-katanya sebelum mengatur gaya",
          paragraphs: [
            "Gunakan kontrol transkrip untuk memperbaiki kata yang salah dengar lalu putar ulang bagian tersebut. Koreksi ejaan tidak perlu menggeser stempel waktu yang sudah pas. Jika kamu menulis ulang bagian naskah yang lebih panjang, cek kembali sinkronisasinya daripada mengira teks baru otomatis cocok dengan ritme lama.",
            "Hapus kesalahan transkripsi yang mengganggu, tetapi pertahankan rincian yang menentukan makna. Sebagai contoh, “lima belas persen” dan “lima puluh persen” sama-sama tampak masuk akal di teks; audiolah yang menentukan mana yang benar di klip akhir.",
          ],
          example: "editor",
        },
        {
          id: "style",
          heading: "3. Pilih gaya subtitle yang nyaman dibaca",
          paragraphs: [
            "Mulai dari preset subtitle, lalu sesuaikan font, ukuran, warna, posisi, dan sorotan kata aktif. Gunakan palet warna yang ringkas dan konsisten. Menyorot setiap kata dengan perubahan warna mencolok bisa bersaing dengan demo di layar atau adegan cepat.",
            "Uji pada frame yang paling sulit, bukan hanya potongan yang bersih. Teks putih yang jelas di atas baju gelap bisa menghilang saat kamera berpindah ke slide terang. Latar kontras atau garis tepi bisa membantu selama tidak menutupi subjek. Simpan gaya hanya setelah menontonnya di sepanjang klip yang mewakili.",
          ],
          bullets: [
            "Pakai font yang tetap jelas dibaca di ukuran layar ponsel.",
            "Pecah subtitle menjadi kelompok kata pendek alih-alih memenuhi seluruh lebar layar.",
            "Jauhkan teks penting dari wajah, grafik, dan kontrol produk.",
            "Terapkan gaya tersimpan secara konsisten, lalu tinjau pengecualian di setiap klip.",
          ],
        },
        {
          id: "safe-area",
          heading: "4. Cek pratinjau terhadap tombol antarmuka platform",
          paragraphs: [
            "Aplikasi video pendek meletakkan elemen antarmuka di sekeliling dan di atas video. Gunakan pratinjau area aman editor sebagai panduan, lalu cek klip hasil ekspor di aplikasi tujuan sebelum terbit. Tidak ada satu posisi subtitle yang cocok untuk semua aplikasi, perangkat, dan jenis rekaman.",
            "Perhatikan khusus area deskripsi di bawah dan deretan tombol di samping. Jika video sumber sudah punya teks di layar, geser atau sederhanakan subtitle agar dua pesan tidak bertumpuk. Posisi ideal juga bisa berubah ketika kamu beralih dari crop wajah ke rekaman layar yang lebih lebar.",
          ],
          table: {
            caption: "Masalah subtitle yang perlu dicek saat pemutaran",
            headers: ["Masalah", "Yang perlu dilihat", "Penyesuaian"],
            rows: [
              ["Kata menghilang", "Pergantian adegan terang dan gelap", "Tingkatkan kontras atau pakai latar yang mudah dibaca"],
              ["Subtitle terasa telat", "Kata yang diucapkan dibanding sorotannya", "Tinjau timing transkrip dan hasil render akhir"],
              ["Teks menutupi aksi", "Wajah, tangan, slide, dan UI game", "Geser subtitle atau kurangi jumlah kata di layar"],
              ["Terlalu banyak gerak", "Animasi saat pembicara bicara cepat", "Pilih gaya lebih tenang dan kelompok teks lebih pendek"],
            ],
          },
        },
        {
          id: "export",
          heading: "5. Ekspor dan tonton tanpa suara",
          paragraphs: [
            "Pilih preset tujuan dan render klipnya. Subtitle yang dirender SupoClip menyatu dengan gambar video, sehingga tetap tampil saat file diunggah. Tonton hasil ekspor dengan suara untuk mengecek sinkronisasi, lalu tanpa suara untuk memastikan topik dan argumennya tetap mudah dipahami.",
            "Subtitle yang menyatu di video tidak bisa dimatikan penonton seperti track subtitle terpisah. Itu juga tidak menggantikan semua fitur aksesibilitas yang mungkin disediakan platform. Bila kamu menambahkan subtitle bawaan platform juga, pastikan keduanya tidak menciptakan teks ganda yang membingungkan.",
          ],
          links: [
            { href: "/youtube-shorts-clipper", label: "Selesaikan dan ekspor YouTube Short" },
            { href: "/blog/podcast-to-shorts", label: "Gunakan subtitle dalam alur klip podcast" },
          ],
        },
      ],
      faqs: [
        {
          question: "Bisakah saya memperbaiki subtitle yang dibuat otomatis?",
          answer:
            "Bisa. Tinjau transkrip di editor klip, perbaiki kata yang salah dengar, dan cek pemutarannya sebelum ekspor. Periksa ulang timing-nya setelah perubahan teks yang besar.",
        },
        {
          question: "Apakah saya perlu gaya berbeda untuk setiap platform?",
          answer:
            "Kamu bisa memakai ulang gaya visual yang sama, tetapi cek posisi dan keterbacaannya di tiap tujuan. Tombol tiap platform dan isi videonya bisa membutuhkan posisi subtitle yang berbeda.",
        },
        {
          question: "Apakah penonton bisa mematikan subtitle ini?",
          answer:
            "Subtitle yang dirender ke gambar video akan selalu terlihat. Ini berbeda dari track subtitle terpisah yang bisa dinyalakan atau dimatikan oleh penonton di platform.",
        },
      ],
      cta: {
        title: "Buat klip berikutnya lebih nyaman diikuti",
        body: "Buat klip, perbaiki transkripnya, dan susun gaya subtitle yang tetap tajam dibaca di ponsel.",
        label: "Buat klip ber-subtitle",
      },
      gallery: {
        heading: "Baca subtitle jadinya saat pembicara berbicara",
        intro:
          "Klip utuh ini ditranskripsi dan dirender melalui SupoClip. Putar dengan suara untuk mengecek sinkronisasi, lalu bisukan pemutar dan nilai keterbacaannya di atas latar yang berubah-ubah.",
        notes: {
          "price-1":
            "Ikuti sorotan kata aktif sepanjang satu kalimat penuh. Nilai teks pada lebar tampilan ponsel yang ditampilkan daripada memperbesar pemutar agar font kecil tampak terbaca.",
          "ton-1":
            "Gunakan pembicara dan rekaman kedua untuk menguji gaya subtitle yang sama. Memakai ulang preset tetap menuntut pengecekan nama, kontras, pemenggalan baris, dan posisi pada tiap klip.",
        },
      },
      editorFigure: {
        ariaLabel: "Buka tangkapan layar penuh editor SupoClip",
        alt: "Editor subtitle SupoClip yang menampilkan kontrol gaya, transkrip per kata, dan pratinjau area aman",
        caption:
          "Tangkapan layar editor dengan video contoh. Kontrol subtitle dan transkrip berada di samping pratinjau; buka gambar untuk melihatnya dalam ukuran penuh.",
      },
    },
  },
};
