import { useI18n } from "../i18n";
import type { SlugState } from "../useSlugCheck";

export function SlugMsg({ state }: { state: SlugState }) {
  const { t } = useI18n();
  if (state === "bad") return <div className="field-msg err">{t("valid.slugFormat")}</div>;
  if (state === "taken") return <div className="field-msg err">{t("valid.slugTaken")}</div>;
  if (state === "free") return <div className="field-msg ok">✓ {t("valid.slugFree")}</div>;
  return null;
}
