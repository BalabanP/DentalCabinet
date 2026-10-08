// Set these (in frontend/.env.production) to talk to the deployed AWS stack and require sign-in.
// With none of them set, the UI runs in local mode: no sign-in, requests go through the Vite proxy.
const env = import.meta.env;

export const API_URL: string = ((env.VITE_API_URL as string | undefined) ?? "").replace(/\/$/, "");
export const USER_POOL_ID: string = (env.VITE_USER_POOL_ID as string | undefined) ?? "";
export const USER_POOL_CLIENT_ID: string = (env.VITE_USER_POOL_CLIENT_ID as string | undefined) ?? "";

export const AUTH_ENABLED = Boolean(API_URL && USER_POOL_ID && USER_POOL_CLIENT_ID);

export const CHAT_URL = AUTH_ENABLED ? `${API_URL}/chat` : "/api/chat";
export const HEALTH_URL = AUTH_ENABLED ? `${API_URL}/chat/health` : "/api/health";
