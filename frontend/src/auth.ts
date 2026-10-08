import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
  CognitoUserSession,
  type IAuthenticationCallback,
} from "amazon-cognito-identity-js";
import { AUTH_ENABLED, USER_POOL_CLIENT_ID, USER_POOL_ID } from "./config";

const pool = AUTH_ENABLED ? new CognitoUserPool({ UserPoolId: USER_POOL_ID, ClientId: USER_POOL_CLIENT_ID }) : null;

/** The stored session, refreshed automatically while the refresh token is valid. */
export function currentSession(): Promise<CognitoUserSession | null> {
  return new Promise((resolve) => {
    const user = pool?.getCurrentUser();
    if (!user) return resolve(null);
    user.getSession((err: Error | null, session: CognitoUserSession | null) =>
      resolve(err || !session || !session.isValid() ? null : session),
    );
  });
}

export async function getIdToken(): Promise<string | null> {
  const session = await currentSession();
  return session ? session.getIdToken().getJwtToken() : null;
}

export function signOut(): void {
  pool?.getCurrentUser()?.signOut();
}

export type SignInStep =
  | { step: "done" }
  | { step: "new_password"; submit: (newPassword: string) => Promise<SignInStep> }
  | { step: "totp"; submit: (code: string) => Promise<SignInStep> }
  | { step: "unsupported"; message: string };

export function signIn(email: string, password: string): Promise<SignInStep> {
  if (!pool) return Promise.reject(new Error("sign-in is not configured"));
  const user = new CognitoUser({ Username: email.trim(), Pool: pool });

  // Every challenge answer resolves the same way, so build the callbacks per promise.
  const callbacks = (resolve: (s: SignInStep) => void, reject: (e: Error) => void): IAuthenticationCallback => ({
    onSuccess: () => resolve({ step: "done" }),
    onFailure: (err: Error) => reject(err),
    newPasswordRequired: () =>
      resolve({
        step: "new_password",
        submit: (newPassword) =>
          new Promise((res, rej) => user.completeNewPasswordChallenge(newPassword, {}, callbacks(res, rej))),
      }),
    totpRequired: () =>
      resolve({
        step: "totp",
        submit: (code) =>
          new Promise((res, rej) => user.sendMFACode(code, callbacks(res, rej), "SOFTWARE_TOKEN_MFA")),
      }),
    mfaSetup: () =>
      resolve({
        step: "unsupported",
        message:
          "This account must enrol an authenticator app first, which this page cannot do yet. " +
          "Ask an administrator to set it up, or deploy with RequireMfa=false.",
      }),
    mfaRequired: () =>
      resolve({ step: "unsupported", message: "SMS codes are not supported. Ask an administrator for help." }),
  });

  return new Promise((resolve, reject) =>
    user.authenticateUser(
      new AuthenticationDetails({ Username: email.trim(), Password: password }),
      callbacks(resolve, reject),
    ),
  );
}
