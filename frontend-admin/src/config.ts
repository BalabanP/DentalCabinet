// With all three set the app requires Cognito sign-in and talks to the deployed API.
// With none set it runs in local mode: no sign-in, requests go through the Vite proxy to the local backend.
const env = import.meta.env;

export const API_URL: string = ((env.VITE_API_URL as string | undefined) ?? "").replace(/\/$/, "");
export const USER_POOL_ID: string = (env.VITE_USER_POOL_ID as string | undefined) ?? "";
export const USER_POOL_CLIENT_ID: string = (env.VITE_USER_POOL_CLIENT_ID as string | undefined) ?? "";

export const AUTH_ENABLED = Boolean(API_URL && USER_POOL_ID && USER_POOL_CLIENT_ID);
export const API_BASE = AUTH_ENABLED ? API_URL : "/api";
