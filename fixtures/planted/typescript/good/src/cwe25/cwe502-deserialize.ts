// CWE-502: restore a user's saved preferences from a cookie as plain, validated JSON.
import type { Request, Response } from "express";

interface Prefs { theme: "light" | "dark"; pageSize: number }

export function loadPrefs(req: Request, res: Response): void {
  const raw = Buffer.from(req.cookies.prefs ?? "", "base64").toString();
  const parsed = JSON.parse(raw || "{}") as Partial<Prefs>;
  const prefs: Prefs = {
    theme: parsed.theme === "dark" ? "dark" : "light",
    pageSize: Math.min(Math.max(Number(parsed.pageSize) || 20, 1), 100),
  };
  res.json(prefs);
}
