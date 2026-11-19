// Chạy trên trang của khách hàng:
//   <script src="https://<host>/widget/loader.js" data-key="wpk_xxx" async></script>
// Chỉ tạo nút nổi + iframe. Không dependency, không chạm CSS/JS của trang (Shadow DOM đóng),
// postMessage chỉ để mở/đóng; token sống trong iframe, không bao giờ đi qua đây (thiết kế §9.1).

type AtlasWindow = Window & { __atlasWebchat?: boolean };

(() => {
  const w = window as AtlasWindow;
  const script = document.currentScript as HTMLScriptElement | null;
  const key = script?.dataset.key;
  if (!script || !key || w.__atlasWebchat) return; // nhúng hai lần: bỏ qua lần sau
  w.__atlasWebchat = true;

  const origin = new URL(script.src).origin; // backend phục vụ loader cũng phục vụ iframe
  const label = script.dataset.label || "Chat";

  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `
    <style>
      :host { all: initial; }
      .btn { position: fixed; right: 20px; bottom: 20px; z-index: 2147483646;
        width: 56px; height: 56px; border: 0; border-radius: 50%; cursor: pointer;
        background: #0b6e4f; color: #fff; box-shadow: 0 4px 14px rgb(0 0 0 / .25); }
      .btn:focus-visible { outline: 3px solid #1a73e8; outline-offset: 3px; }
      .btn svg { width: 26px; height: 26px; fill: currentColor; }
      .frame { position: fixed; right: 20px; bottom: 88px; z-index: 2147483647;
        width: 380px; height: min(600px, calc(100vh - 110px)); border: 0; border-radius: 16px;
        box-shadow: 0 8px 30px rgb(0 0 0 / .25); background: #fff; display: none; }
      .frame.open { display: block; }
      @media (max-width: 480px) {
        .frame { inset: 0; width: 100%; height: 100%; border-radius: 0; }
        .frame.open + .btn, .btn.hidden { display: none; }
      }
    </style>
    <button class="btn" type="button" aria-expanded="false"></button>`;
  const button = root.querySelector("button") as HTMLButtonElement;
  button.setAttribute("aria-label", label);
  button.innerHTML =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2z"/></svg>';

  let frame: HTMLIFrameElement | null = null;

  // Tạo iframe khi mở lần đầu: trang không dùng chat thì không tải gì thêm.
  const ensureFrame = (): HTMLIFrameElement => {
    if (frame) return frame;
    frame = document.createElement("iframe");
    frame.className = "frame";
    frame.title = label;
    frame.src = `${origin}/w/${encodeURIComponent(key)}`;
    frame.setAttribute("referrerpolicy", "strict-origin");
    root.insertBefore(frame, button);
    return frame;
  };

  const setOpen = (open: boolean) => {
    const f = ensureFrame();
    f.classList.toggle("open", open);
    button.classList.toggle("hidden", open);
    button.setAttribute("aria-expanded", String(open));
    if (open) {
      const focus = () => f.contentWindow?.postMessage({ type: "atlas:open" }, origin);
      f.addEventListener("load", focus, { once: true });
      focus();
    } else {
      button.focus();
    }
  };

  button.addEventListener("click", () => setOpen(button.getAttribute("aria-expanded") !== "true"));

  window.addEventListener("message", (e: MessageEvent) => {
    // Chỉ tin đúng iframe của mình, đúng origin backend.
    if (!frame || e.source !== frame.contentWindow || e.origin !== origin) return;
    if (e.data?.type === "atlas:close") setOpen(false);
  });

  document.body.appendChild(host);
})();
