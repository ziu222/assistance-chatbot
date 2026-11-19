import { useState, type FormEvent } from "react";
import { ApiError } from "../../shared/api/client";
import { useLogin } from "./api";

function errorText(e: unknown): string {
  if (!(e instanceof ApiError)) return "Không kết nối được máy chủ.";
  if (e.code === "invalid_credentials") return "Email hoặc mật khẩu không đúng.";
  if (e.code === "rate_limited") return `Thử quá nhiều lần. Vui lòng đợi ${e.retryAfter ?? 60} giây.`;
  if (e.code === "tenant_required") return "Tài khoản thuộc nhiều workspace, hãy nhập mã workspace.";
  return "Đăng nhập không thành công.";
}

export function LoginForm() {
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenant, setTenant] = useState("");
  const needsTenant = login.error instanceof ApiError && login.error.code === "tenant_required";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ email, password, ...(tenant ? { tenant_slug: tenant } : {}) });
  };

  return (
    <main className="login">
      <form className="card" onSubmit={submit} aria-describedby={login.error ? "login-error" : undefined}>
        <h1>Hộp thư hỗ trợ</h1>
        <label>
          Email
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Mật khẩu
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {(needsTenant || tenant) && (
          <label>
            Mã workspace
            <input value={tenant} onChange={(e) => setTenant(e.target.value)} />
          </label>
        )}
        {login.error && (
          <p id="login-error" className="error" role="alert">
            {errorText(login.error)}
          </p>
        )}
        <button type="submit" disabled={login.isPending}>
          {login.isPending ? "Đang đăng nhập…" : "Đăng nhập"}
        </button>
      </form>
    </main>
  );
}
