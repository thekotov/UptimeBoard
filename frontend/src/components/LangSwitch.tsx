import { useI18n } from "../i18n";

export function LangSwitch() {
  const { lang, setLang } = useI18n();
  return (
    <div className="lang-switch" role="group" aria-label="language">
      <button
        className={lang === "ru" ? "active" : ""}
        onClick={() => setLang("ru")}
        type="button"
      >
        РУ
      </button>
      <button
        className={lang === "en" ? "active" : ""}
        onClick={() => setLang("en")}
        type="button"
      >
        EN
      </button>
    </div>
  );
}
