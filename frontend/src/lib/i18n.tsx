import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type Lang = "de" | "en";

interface LangCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
}

const Ctx = createContext<LangCtx>({ lang: "en", setLang: () => {} });

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>("en");

  // Read localStorage after mount to avoid SSR hydration mismatch.
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem("oc8-lang");
      if (stored === "de" || stored === "en") setLangState(stored);
    } catch {
      /* noop */
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem("oc8-lang", lang);
      document.documentElement.lang = lang;
    } catch {
      /* noop */
    }
  }, [lang]);

  const value = useMemo(
    () => ({ lang, setLang: setLangState }),
    [lang],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLang() {
  return useContext(Ctx);
}

/**
 * Inline translator. Usage: `t("Departments", "Abteilungen")`.
 * Falls back to the English string if the German is missing.
 */
export function useT() {
  const { lang } = useContext(Ctx);
  return useCallback(
    (en: string, de: string) => (lang === "de" ? de : en),
    [lang],
  );
}