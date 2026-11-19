import { createRoot } from "react-dom/client";
import { App } from "./App";
import { lang } from "./i18n";
import "./styles.css";

// Trang iframe là /w/{public_key}; key do server đã xác nhận khi phục vụ trang.
const publicKey = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() ?? "");
document.documentElement.lang = lang;
createRoot(document.getElementById("root")!).render(<App publicKey={publicKey} />);
