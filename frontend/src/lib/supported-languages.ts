/** Universal-2's published language table, including regional variants.
 * Source: https://www.assemblyai.com/docs/pre-recorded-audio/supported-languages
 * Verified 2026-11-05. This describes hosted transcription, not UI locales or
 * the separately configurable providers available when self-hosting.
 */
export const LANGUAGE_SUPPORT_SOURCE = "https://www.assemblyai.com/docs/pre-recorded-audio/supported-languages";

export const SUPPORTED_LANGUAGES = [
  { code: "af", name: "Afrikaans" },
  { code: "sq", name: "Albanian" },
  { code: "am", name: "Amharic" },
  { code: "ar", name: "Arabic" },
  { code: "hy", name: "Armenian" },
  { code: "as", name: "Assamese" },
  { code: "en_au", name: "Australian English" },
  { code: "az", name: "Azerbaijani" },
  { code: "ba", name: "Bashkir" },
  { code: "eu", name: "Basque" },
  { code: "be", name: "Belarusian" },
  { code: "bn", name: "Bengali" },
  { code: "bs", name: "Bosnian" },
  { code: "br", name: "Breton" },
  { code: "en_uk", name: "British English" },
  { code: "bg", name: "Bulgarian" },
  { code: "my", name: "Burmese" },
  { code: "ca", name: "Catalan" },
  { code: "zh", name: "Chinese (Mandarin)" },
  { code: "hr", name: "Croatian" },
  { code: "cs", name: "Czech" },
  { code: "da", name: "Danish" },
  { code: "nl", name: "Dutch" },
  { code: "en", name: "English" },
  { code: "et", name: "Estonian" },
  { code: "fo", name: "Faroese" },
  { code: "fi", name: "Finnish" },
  { code: "fr", name: "French" },
  { code: "gl", name: "Galician" },
  { code: "ka", name: "Georgian" },
  { code: "de", name: "German" },
  { code: "el", name: "Greek" },
  { code: "gu", name: "Gujarati" },
  { code: "ht", name: "Haitian Creole" },
  { code: "ha", name: "Hausa" },
  { code: "haw", name: "Hawaiian" },
  { code: "he", name: "Hebrew" },
  { code: "hi", name: "Hindi" },
  { code: "hu", name: "Hungarian" },
  { code: "is", name: "Icelandic" },
  { code: "id", name: "Indonesian" },
  { code: "it", name: "Italian" },
  { code: "ja", name: "Japanese" },
  { code: "jw", name: "Javanese" },
  { code: "kn", name: "Kannada" },
  { code: "kk", name: "Kazakh" },
  { code: "km", name: "Khmer" },
  { code: "ko", name: "Korean" },
  { code: "lo", name: "Lao" },
  { code: "la", name: "Latin" },
  { code: "lv", name: "Latvian" },
  { code: "ln", name: "Lingala" },
  { code: "lt", name: "Lithuanian" },
  { code: "lb", name: "Luxembourgish" },
  { code: "mk", name: "Macedonian" },
  { code: "mg", name: "Malagasy" },
  { code: "ms", name: "Malay" },
  { code: "ml", name: "Malayalam" },
  { code: "mt", name: "Maltese" },
  { code: "mi", name: "Maori" },
  { code: "mr", name: "Marathi" },
  { code: "mn", name: "Mongolian" },
  { code: "ne", name: "Nepali" },
  { code: "no", name: "Norwegian" },
  { code: "nn", name: "Norwegian Nynorsk" },
  { code: "oc", name: "Occitan" },
  { code: "ps", name: "Pashto" },
  { code: "fa", name: "Persian" },
  { code: "pl", name: "Polish" },
  { code: "pt", name: "Portuguese" },
  { code: "pa", name: "Punjabi" },
  { code: "ro", name: "Romanian" },
  { code: "ru", name: "Russian" },
  { code: "sa", name: "Sanskrit" },
  { code: "sr", name: "Serbian" },
  { code: "sn", name: "Shona" },
  { code: "sd", name: "Sindhi" },
  { code: "si", name: "Sinhala" },
  { code: "sk", name: "Slovak" },
  { code: "sl", name: "Slovenian" },
  { code: "so", name: "Somali" },
  { code: "es", name: "Spanish" },
  { code: "su", name: "Sundanese" },
  { code: "sw", name: "Swahili" },
  { code: "sv", name: "Swedish" },
  { code: "de_ch", name: "Swiss German" },
  { code: "tl", name: "Tagalog" },
  { code: "tg", name: "Tajik" },
  { code: "ta", name: "Tamil" },
  { code: "tt", name: "Tatar" },
  { code: "te", name: "Telugu" },
  { code: "th", name: "Thai" },
  { code: "bo", name: "Tibetan" },
  { code: "tr", name: "Turkish" },
  { code: "tk", name: "Turkmen" },
  { code: "en_us", name: "US English" },
  { code: "uk", name: "Ukrainian" },
  { code: "ur", name: "Urdu" },
  { code: "uz", name: "Uzbek" },
  { code: "vi", name: "Vietnamese" },
  { code: "cy", name: "Welsh" },
  { code: "yi", name: "Yiddish" },
  { code: "yo", name: "Yoruba" },
] as const;

export function languageTag(code: string) {
  if (code === "de_ch") return "gsw";
  if (code === "jw") return "jv";
  if (code === "en_uk") return "en-GB";
  return code.replace("_", "-");
}

export interface SupportedLanguageLabel {
  code: string;
  name: string;
  label: string;
  nativeName: string;
  tag: string;
}

/** Resolve names on the server; browser and Node ICU data can differ. */
export function getSupportedLanguageLabels(locale: string, mandarinLabel: string): SupportedLanguageLabel[] {
  const names = new Intl.DisplayNames([locale], { type: "language", fallback: "none" });
  return SUPPORTED_LANGUAGES.map(({ code, name }) => {
    const tag = languageTag(code);
    const label = code === "zh" ? mandarinLabel : names.of(tag) ?? name;
    const nativeName = new Intl.DisplayNames([tag, locale], { type: "language", fallback: "none" }).of(tag) ?? name;
    return { code, name, label, nativeName, tag };
  }).sort((a, b) => a.label.localeCompare(b.label, locale));
}
