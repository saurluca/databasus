interface RuntimeConfig {
  IS_CLOUD?: string;
  GITHUB_CLIENT_ID?: string;
  GOOGLE_CLIENT_ID?: string;
  IS_EMAIL_CONFIGURED?: string;
  CLOUDFLARE_TURNSTILE_SITE_KEY?: string;
}

declare global {
  interface Window {
    __RUNTIME_CONFIG__?: RuntimeConfig;
  }
}

export function getApplicationServer() {
  const origin = window.location.origin;
  const url = new URL(origin);

  const isDevelopment = import.meta.env.MODE === 'development';

  if (isDevelopment) {
    return `${url.protocol}//${url.hostname}:4005`;
  } else {
    return `${url.protocol}//${url.hostname}:${url.port || (url.protocol === 'https:' ? '443' : '80')}`;
  }
}

export const APP_VERSION = (import.meta.env.VITE_APP_VERSION as string) || 'dev';

export const IS_CLOUD =
  window.__RUNTIME_CONFIG__?.IS_CLOUD === 'true' || import.meta.env.VITE_IS_CLOUD === 'true';

export const GITHUB_CLIENT_ID =
  window.__RUNTIME_CONFIG__?.GITHUB_CLIENT_ID || import.meta.env.VITE_GITHUB_CLIENT_ID || '';

export const GOOGLE_CLIENT_ID =
  window.__RUNTIME_CONFIG__?.GOOGLE_CLIENT_ID || import.meta.env.VITE_GOOGLE_CLIENT_ID || '';

export const IS_EMAIL_CONFIGURED =
  window.__RUNTIME_CONFIG__?.IS_EMAIL_CONFIGURED === 'true' ||
  import.meta.env.VITE_IS_EMAIL_CONFIGURED === 'true';

export const CLOUDFLARE_TURNSTILE_SITE_KEY =
  window.__RUNTIME_CONFIG__?.CLOUDFLARE_TURNSTILE_SITE_KEY ||
  import.meta.env.VITE_CLOUDFLARE_TURNSTILE_SITE_KEY ||
  '';

export function getOAuthRedirectUri(): string {
  return `${window.location.origin}/auth/callback`;
}
