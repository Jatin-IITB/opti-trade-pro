interface Props {
  data: {
    standard: Record<string, number>;
    higherOrder: Record<string, number>;
    params: {
      spot: number;
      strike: number;
      expiry: number;
      vol: number;
      rate: number;
      div: number;
    };
  };
}

const STANDARD_META: Record<string, { label: string; desc: string }> = {
  delta: { label: "Delta", desc: "dV/dS — directional exposure" },
  gamma: { label: "Gamma", desc: "d²V/dS² — convexity" },
  vega: { label: "Vega", desc: "dV/dσ — vol sensitivity" },
  theta: { label: "Theta", desc: "dV/dt — time decay" },
  rho: { label: "Rho", desc: "dV/dr — rate sensitivity" },
  vanna: { label: "Vanna", desc: "d²V/dSdσ — delta-vol cross" },
  volga: { label: "Volga", desc: "d²V/dσ² — vol convexity" },
};

const HIGHER_META: Record<string, { label: string; desc: string; order: string }> = {
  charm: { label: "Charm", desc: "d²V/dSdt — delta decay", order: "2nd" },
  veta: { label: "Veta", desc: "d²V/dσdt — vega decay", order: "2nd" },
  speed: { label: "Speed", desc: "d³V/dS³ — gamma sensitivity", order: "3rd" },
  color: { label: "Color", desc: "d³V/dS²dt — gamma decay", order: "3rd" },
  ultima: { label: "Ultima", desc: "d³V/dσ³ — vol of vol of vol", order: "3rd" },
  zomma: { label: "Zomma", desc: "d³V/dS²dσ — gamma-vol cross", order: "3rd" },
};

function formatGreek(value: number): string {
  const abs = Math.abs(value);
  if (abs === 0) return "0";
  if (abs < 0.0001) return value.toExponential(3);
  if (abs < 1) return value.toFixed(6);
  return value.toFixed(4);
}

export function HigherOrderGreeks({ data }: Props) {
  const { params } = data;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-100">
            Higher-Order Greeks
          </h2>
          <p className="text-sm text-slate-400">
            JAX automatic differentiation — exact all-order derivatives via
            nested <code className="text-teal-400">jax.grad</code>
          </p>
        </div>
        <div className="px-3 py-1.5 rounded-lg bg-teal-900/30 border border-teal-700/50">
          <span className="text-xs text-teal-400 font-mono">JAX AD Only</span>
        </div>
      </div>

      <div className="bg-slate-800/30 rounded-xl border border-slate-700/50 p-4">
        <h3 className="text-xs font-medium text-slate-400 uppercase tracking-wider mb-3">
          Contract Parameters
        </h3>
        <div className="grid grid-cols-6 gap-4">
          {[
            { label: "Spot", value: params.spot.toLocaleString() },
            { label: "Strike", value: params.strike.toLocaleString() },
            { label: "Expiry", value: `${(params.expiry * 365).toFixed(0)}d` },
            { label: "Vol", value: `${(params.vol * 100).toFixed(1)}%` },
            { label: "Rate", value: `${(params.rate * 100).toFixed(1)}%` },
            { label: "Div", value: `${(params.div * 100).toFixed(1)}%` },
          ].map(({ label, value }) => (
            <div key={label} className="text-center">
              <div className="text-xs text-slate-500">{label}</div>
              <div className="text-sm font-mono text-slate-200">{value}</div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-slate-300 mb-3">
          Standard Greeks (1st &amp; 2nd order)
        </h3>
        <div className="grid grid-cols-4 gap-3">
          {Object.entries(STANDARD_META).map(([key, meta]) => {
            const value = data.standard[key];
            if (value === undefined) return null;
            return (
              <div
                key={key}
                className="bg-slate-800/50 rounded-lg border border-slate-700 p-4"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-slate-200">
                    {meta.label}
                  </span>
                  <span className="text-xs px-1.5 py-0.5 rounded bg-slate-700 text-slate-400">
                    {key === "vanna" || key === "volga" ? "2nd" : "1st"}
                  </span>
                </div>
                <div className="text-lg font-mono text-slate-100">
                  {formatGreek(value)}
                </div>
                <div className="text-xs text-slate-500 mt-1">{meta.desc}</div>
              </div>
            );
          })}
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-slate-300 mb-3">
          Higher-Order Greeks (2nd &amp; 3rd order) —{" "}
          <span className="text-teal-400">JAX exclusive</span>
        </h3>
        <div className="grid grid-cols-3 gap-3">
          {Object.entries(HIGHER_META).map(([key, meta]) => {
            const value = data.higherOrder[key];
            if (value === undefined) return null;
            return (
              <div
                key={key}
                className="bg-gradient-to-br from-slate-800/80 to-teal-900/20 rounded-lg border border-teal-800/30 p-4"
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-teal-300">
                    {meta.label}
                  </span>
                  <span className="text-xs px-1.5 py-0.5 rounded bg-teal-900/50 text-teal-400">
                    {meta.order}
                  </span>
                </div>
                <div className="text-lg font-mono text-slate-100">
                  {formatGreek(value)}
                </div>
                <div className="text-xs text-slate-500 mt-1">{meta.desc}</div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="bg-slate-800/20 rounded-lg border border-slate-700/30 p-4 text-xs text-slate-500">
        <strong className="text-slate-400">How it works:</strong> Each Greek is
        computed by nesting <code className="text-teal-400">jax.grad()</code>{" "}
        calls — e.g. Speed = <code>grad(grad(grad(price, S), S), S)</code>.
        Unlike finite-difference, JAX AD gives machine-precision derivatives
        with no step-size tuning. All 13 Greeks are computed in a single
        vectorized pass via <code className="text-teal-400">jax.vmap</code>.
      </div>
    </div>
  );
}
