const intFmt = new Intl.NumberFormat("en-US");
const moneyFmt = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 });
const money2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

export const fmt = {
  int: (n: number) => intFmt.format(Math.round(n)),
  money: (n: number) => moneyFmt.format(n),
  money2: (n: number) => money2.format(n),
  ms: (n: number) => `${Math.round(n).toLocaleString("en-US")} ms`,
  pct: (n: number) => `${n.toFixed(1)}%`,
  gbps: (n: number) => `${n.toLocaleString("en-US")} Gbps`,
};
