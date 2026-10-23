// CWE-352: the CSRF check switched off for the methods that change state, and a
// cross-site session cookie in an app with no token check.
import express from "express";
import csrf from "csurf";

export const csrfProtection = csrf({ cookie: true, ignoreMethods: ["GET", "HEAD", "OPTIONS", "POST"] }); // expect: arbiter/typescript.csrf-unsafe-methods-ignored
