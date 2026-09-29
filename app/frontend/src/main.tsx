import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initializeAuth } from "./auth";
import { I18nProvider } from "./i18n";
import "./styles.css";

async function bootstrap(): Promise<void> {
  await initializeAuth();
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <I18nProvider>
        <App />
      </I18nProvider>
    </StrictMode>,
  );
}

void bootstrap().catch((error: unknown) => {
  document.body.textContent = error instanceof Error ? error.message : "Unable to start the app.";
});
