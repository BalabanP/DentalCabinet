import React from "react";
import ReactDOM from "react-dom/client";
import { I18nProvider } from "./i18n";
import Root from "./Root";
import { NoticeProvider } from "./ui";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <I18nProvider>
      <NoticeProvider>
        <Root />
      </NoticeProvider>
    </I18nProvider>
  </React.StrictMode>,
);
