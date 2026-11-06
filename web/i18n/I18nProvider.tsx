"use client";

import { useEffect, useState } from "react";
import i18n from "i18next";

import {
  ensureLanguage,
  initI18n,
  isLanguageReady,
  normalizeLanguage,
  type AppLanguage,
} from "./init";

// Initialize i18next at module load (before any React render) so that the
// `init()` call — which fires `initialized` / `languageChanged` events that
// trigger setState on subscribed components — never happens during another
// component's render phase. Calling `initI18n` from the render body would
// produce the React warning:
//   "Cannot update a component (`X`) while rendering a different component
//   (`I18nProvider`)."
initI18n();

export function I18nProvider({
  language,
  enabled = true,
  children,
}: {
  language: AppLanguage | string;
  enabled?: boolean;
  children: React.ReactNode;
}) {
  const nextLang = normalizeLanguage(language);
  const [failedLanguage, setFailedLanguage] = useState<AppLanguage | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [readyLanguage, setReadyLanguage] = useState<AppLanguage | null>(() =>
    enabled &&
    i18n.language === nextLang &&
    isLanguageReady(nextLang)
      ? nextLang
      : null,
  );

  useEffect(() => {
    let cancelled = false;
    if (!enabled) return () => undefined;
    void ensureLanguage(nextLang).then(async () => {
      if (cancelled) return;
      if (i18n.language !== nextLang) {
        await i18n.changeLanguage(nextLang);
      }
      if (cancelled) return;
      // Keep <html lang="..."> in sync for accessibility & Intl defaults.
      document.documentElement.lang = nextLang;
      setFailedLanguage(null);
      setReadyLanguage(nextLang);
    }).catch(() => {
      if (!cancelled) setFailedLanguage(nextLang);
    });
    return () => {
      cancelled = true;
    };
  }, [enabled, nextLang, attempt]);

  if (enabled && failedLanguage === nextLang) {
    // Bootstrap recovery must remain readable even if the chosen language
    // bundle itself is unavailable.
    const copy = nextLang === "zh"
      ? { message: "暂时无法打开界面，请重试。", retry: "重试" }
      : { message: "The interface could not load. Please try again.", retry: "Retry" };
    return (
      <div className="flex min-h-dvh items-center justify-center bg-background px-6">
        <div role="alert" className="max-w-sm text-center text-sm text-foreground">
          <p>{copy.message}</p>
          <button
            type="button"
            className="mt-4 rounded-lg border border-border bg-card px-4 py-2 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => {
              setFailedLanguage(null);
              setAttempt(value => value + 1);
            }}
          >
            {copy.retry}
          </button>
        </div>
      </div>
    );
  }

  if (!enabled || readyLanguage !== nextLang) {
    return (
      <div
        aria-busy="true"
        aria-hidden="true"
        className="min-h-dvh bg-[var(--background)]"
      />
    );
  }
  return children;
}
