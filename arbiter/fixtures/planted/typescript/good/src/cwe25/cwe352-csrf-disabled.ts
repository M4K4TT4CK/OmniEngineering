// CWE-352: the CSRF check on every unsafe method (the default ignore list).
import express from "express";
import csrf from "csurf";

export const csrfProtection = csrf({ cookie: true, ignoreMethods: ["GET", "HEAD", "OPTIONS"] });
