import { useCallback, useEffect, useState } from "react";

export interface AuthStatus {
  authenticated: boolean;
  user_id: string | null;
  token_expires_at: string | null;
  needs_refresh: boolean;
  last_validated: string | null;
}

interface AuthState {
  status: AuthStatus | null;
  loading: boolean;
  error: string | null;
}

export function useAuthStatus(): AuthState & { refresh: () => void } {
  const [state, setState] = useState<AuthState>({
    status: null,
    loading: true,
    error: null,
  });

  const refresh = useCallback(async () => {
    try {
      const resp = await fetch("/api/v1/auth/status");
      if (!resp.ok) throw new Error(`${resp.status}`);
      const data = (await resp.json()) as AuthStatus;
      setState({ status: data, loading: false, error: null });
    } catch (e) {
      setState({
        status: null,
        loading: false,
        error: e instanceof Error ? e.message : "Failed to fetch auth status",
      });
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { ...state, refresh };
}
