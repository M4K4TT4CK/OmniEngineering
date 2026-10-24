package main

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/mail"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"golang.org/x/crypto/bcrypt"
)

const (
	RoleAdmin  = "admin"
	RoleEditor = "editor"
	RoleViewer = "viewer"
)

// roleRank orders roles so a handler can require "at least" a given role.
var roleRank = map[string]int{RoleViewer: 1, RoleEditor: 2, RoleAdmin: 3}

const (
	sessionTTL     = 24 * time.Hour
	inviteTTL      = 7 * 24 * time.Hour
	maxBodyBytes   = 1 << 20
	maxNameLen     = 200
	minPasswordLen = 8
	maxPasswordLen = 72 // bcrypt ignores everything past 72 bytes
	defaultLimit   = 50
	maxLimit       = 100
)

type Server struct {
	store      *Store
	mux        *http.ServeMux
	bcryptCost int
	dummyHash  []byte
	now        func() time.Time
}

func NewServer(store *Store, bcryptCost int) (*Server, error) {
	dummy, err := bcrypt.GenerateFromPassword([]byte("dummy-password-for-timing"), bcryptCost)
	if err != nil {
		return nil, err
	}
	s := &Server{
		store:      store,
		mux:        http.NewServeMux(),
		bcryptCost: bcryptCost,
		dummyHash:  dummy,
		now:        time.Now,
	}
	s.routes()
	return s, nil
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) { s.mux.ServeHTTP(w, r) }

func (s *Server) routes() {
	s.mux.HandleFunc("POST /signup", s.handleSignup)
	s.mux.HandleFunc("POST /login", s.handleLogin)
	s.mux.HandleFunc("POST /invites/accept", s.handleAcceptInvite)

	s.mux.HandleFunc("POST /logout", s.authed(RoleViewer, s.handleLogout))
	s.mux.HandleFunc("GET /me", s.authed(RoleViewer, s.handleMe))

	s.mux.HandleFunc("POST /invites", s.authed(RoleAdmin, s.handleCreateInvite))
	s.mux.HandleFunc("GET /users", s.authed(RoleAdmin, s.handleListUsers))
	s.mux.HandleFunc("PATCH /users/{id}", s.authed(RoleAdmin, s.handleUpdateRole))

	s.mux.HandleFunc("GET /notes", s.authed(RoleViewer, s.handleListNotes))
	s.mux.HandleFunc("GET /notes/{id}", s.authed(RoleViewer, s.handleGetNote))
	s.mux.HandleFunc("POST /notes", s.authed(RoleEditor, s.handleCreateNote))
	s.mux.HandleFunc("PUT /notes/{id}", s.authed(RoleEditor, s.handleUpdateNote))
	s.mux.HandleFunc("DELETE /notes/{id}", s.authed(RoleEditor, s.handleDeleteNote))
}

type authedHandler func(w http.ResponseWriter, r *http.Request, u User)

// authed resolves the bearer token to a user and requires at least minRole.
// Handlers receive the user and must scope every query to u.OrgID.
func (s *Server) authed(minRole string, h authedHandler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		token, ok := bearerToken(r)
		if !ok {
			w.Header().Set("WWW-Authenticate", "Bearer")
			writeError(w, http.StatusUnauthorized, "missing bearer token")
			return
		}
		u, err := s.store.UserBySession(r.Context(), hashToken(token), s.now())
		if errors.Is(err, ErrNotFound) {
			w.Header().Set("WWW-Authenticate", "Bearer")
			writeError(w, http.StatusUnauthorized, "invalid or expired token")
			return
		}
		if err != nil {
			internalError(w, err)
			return
		}
		if roleRank[u.Role] < roleRank[minRole] {
			writeError(w, http.StatusForbidden, "insufficient role")
			return
		}
		h(w, r, u)
	}
}

// --- auth ---

func (s *Server) handleSignup(w http.ResponseWriter, r *http.Request) {
	var req struct {
		OrgName  string `json:"org_name"`
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if !decodeJSON(w, r, &req) {
		return
	}
	orgName := strings.TrimSpace(req.OrgName)
	if orgName == "" || utf8.RuneCountInString(orgName) > maxNameLen {
		writeError(w, http.StatusBadRequest, "org_name is required and must be at most 200 characters")
		return
	}
	email, ok := normaliseEmail(req.Email)
	if !ok {
		writeError(w, http.StatusBadRequest, "invalid email")
		return
	}
	if msg := checkPassword(req.Password); msg != "" {
		writeError(w, http.StatusBadRequest, msg)
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), s.bcryptCost)
	if err != nil {
		internalError(w, err)
		return
	}
	u, err := s.store.CreateOrgWithAdmin(r.Context(), orgName, email, string(hash), s.now())
	if errors.Is(err, ErrConflict) {
		writeError(w, http.StatusConflict, "email already registered")
		return
	}
	if err != nil {
		internalError(w, err)
		return
	}
	s.issueSession(w, r, u, http.StatusCreated)
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if !decodeJSON(w, r, &req) {
		return
	}
	email, _ := normaliseEmail(req.Email)
	u, hash, err := s.store.UserByEmail(r.Context(), email)
	if errors.Is(err, ErrNotFound) {
		// Do the same bcrypt work as a real login so response time doesn't
		// reveal which emails are registered.
		_ = bcrypt.CompareHashAndPassword(s.dummyHash, []byte(req.Password))
		writeError(w, http.StatusUnauthorized, "invalid email or password")
		return
	}
	if err != nil {
		internalError(w, err)
		return
	}
	if bcrypt.CompareHashAndPassword([]byte(hash), []byte(req.Password)) != nil {
		writeError(w, http.StatusUnauthorized, "invalid email or password")
		return
	}
	s.issueSession(w, r, u, http.StatusOK)
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request, _ User) {
	token, _ := bearerToken(r)
	if err := s.store.DeleteSession(r.Context(), hashToken(token)); err != nil {
		internalError(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleMe(w http.ResponseWriter, _ *http.Request, u User) {
	writeJSON(w, http.StatusOK, u)
}

func (s *Server) issueSession(w http.ResponseWriter, r *http.Request, u User, status int) {
	token, err := newToken()
	if err != nil {
		internalError(w, err)
		return
	}
	now := s.now()
	expires := now.Add(sessionTTL)
	if err := s.store.CreateSession(r.Context(), hashToken(token), u.ID, expires, now); err != nil {
		internalError(w, err)
		return
	}
	writeJSON(w, status, map[string]any{
		"token":      token,
		"expires_at": timestamp(expires),
		"user":       u,
	})
}

// --- invites and users ---

func (s *Server) handleCreateInvite(w http.ResponseWriter, r *http.Request, u User) {
	var req struct {
		Email string `json:"email"`
		Role  string `json:"role"`
	}
	if !decodeJSON(w, r, &req) {
		return
	}
	email, ok := normaliseEmail(req.Email)
	if !ok {
		writeError(w, http.StatusBadRequest, "invalid email")
		return
	}
	if roleRank[req.Role] == 0 {
		writeError(w, http.StatusBadRequest, "role must be admin, editor or viewer")
		return
	}
	token, err := newToken()
	if err != nil {
		internalError(w, err)
		return
	}
	expires := s.now().Add(inviteTTL)
	err = s.store.CreateInvite(r.Context(), hashToken(token), u.OrgID, email, req.Role, u.ID, expires)
	if errors.Is(err, ErrConflict) {
		writeError(w, http.StatusConflict, "a user with that email already exists")
		return
	}
	if err != nil {
		internalError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{
		"invite_token": token,
		"email":        email,
		"role":         req.Role,
		"expires_at":   timestamp(expires),
	})
}

func (s *Server) handleAcceptInvite(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Token    string `json:"token"`
		Password string `json:"password"`
	}
	if !decodeJSON(w, r, &req) {
		return
	}
	if req.Token == "" {
		writeError(w, http.StatusBadRequest, "token is required")
		return
	}
	if msg := checkPassword(req.Password); msg != "" {
		writeError(w, http.StatusBadRequest, msg)
		return
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), s.bcryptCost)
	if err != nil {
		internalError(w, err)
		return
	}
	u, err := s.store.AcceptInvite(r.Context(), hashToken(req.Token), string(hash), s.now())
	if errors.Is(err, ErrNotFound) {
		writeError(w, http.StatusNotFound, "invalid or expired invite")
		return
	}
	if errors.Is(err, ErrConflict) {
		writeError(w, http.StatusConflict, "email already registered")
		return
	}
	if err != nil {
		internalError(w, err)
		return
	}
	s.issueSession(w, r, u, http.StatusCreated)
}

func (s *Server) handleListUsers(w http.ResponseWriter, r *http.Request, u User) {
	users, err := s.store.ListUsers(r.Context(), u.OrgID)
	if err != nil {
		internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"users": users})
}

func (s *Server) handleUpdateRole(w http.ResponseWriter, r *http.Request, u User) {
	id, ok := pathID(r)
	if !ok {
		writeError(w, http.StatusNotFound, "user not found")
		return
	}
	var req struct {
		Role string `json:"role"`
	}
	if !decodeJSON(w, r, &req) {
		return
	}
	if roleRank[req.Role] == 0 {
		writeError(w, http.StatusBadRequest, "role must be admin, editor or viewer")
		return
	}
	updated, err := s.store.UpdateRole(r.Context(), u.OrgID, id, req.Role)
	switch {
	case errors.Is(err, ErrNotFound):
		writeError(w, http.StatusNotFound, "user not found")
	case errors.Is(err, ErrLastAdmin):
		writeError(w, http.StatusConflict, err.Error())
	case err != nil:
		internalError(w, err)
	default:
		writeJSON(w, http.StatusOK, updated)
	}
}

// --- notes ---

type noteInput struct {
	Title string `json:"title"`
	Body  string `json:"body"`
}

func (in *noteInput) validate() string {
	in.Title = strings.TrimSpace(in.Title)
	if in.Title == "" || utf8.RuneCountInString(in.Title) > maxNameLen {
		return "title is required and must be at most 200 characters"
	}
	return ""
}

func (s *Server) handleListNotes(w http.ResponseWriter, r *http.Request, u User) {
	q := r.URL.Query()
	limit, ok := intParam(q.Get("limit"), defaultLimit, 1, maxLimit)
	if !ok {
		writeError(w, http.StatusBadRequest, "limit must be between 1 and 100")
		return
	}
	offset, ok := intParam(q.Get("offset"), 0, 0, 1<<31-1)
	if !ok {
		writeError(w, http.StatusBadRequest, "offset must be a non-negative integer")
		return
	}
	notes, err := s.store.ListNotes(r.Context(), u.OrgID, strings.TrimSpace(q.Get("q")), limit, offset)
	if err != nil {
		internalError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"notes": notes})
}

func (s *Server) handleGetNote(w http.ResponseWriter, r *http.Request, u User) {
	id, ok := pathID(r)
	if !ok {
		writeError(w, http.StatusNotFound, "note not found")
		return
	}
	n, err := s.store.GetNote(r.Context(), u.OrgID, id)
	s.writeNoteResult(w, http.StatusOK, n, err)
}

func (s *Server) handleCreateNote(w http.ResponseWriter, r *http.Request, u User) {
	var in noteInput
	if !decodeJSON(w, r, &in) {
		return
	}
	if msg := in.validate(); msg != "" {
		writeError(w, http.StatusBadRequest, msg)
		return
	}
	n, err := s.store.CreateNote(r.Context(), u.OrgID, u.ID, in.Title, in.Body, s.now())
	s.writeNoteResult(w, http.StatusCreated, n, err)
}

func (s *Server) handleUpdateNote(w http.ResponseWriter, r *http.Request, u User) {
	id, ok := pathID(r)
	if !ok {
		writeError(w, http.StatusNotFound, "note not found")
		return
	}
	var in noteInput
	if !decodeJSON(w, r, &in) {
		return
	}
	if msg := in.validate(); msg != "" {
		writeError(w, http.StatusBadRequest, msg)
		return
	}
	n, err := s.store.UpdateNote(r.Context(), u.OrgID, id, in.Title, in.Body, s.now())
	s.writeNoteResult(w, http.StatusOK, n, err)
}

func (s *Server) handleDeleteNote(w http.ResponseWriter, r *http.Request, u User) {
	id, ok := pathID(r)
	if !ok {
		writeError(w, http.StatusNotFound, "note not found")
		return
	}
	err := s.store.DeleteNote(r.Context(), u.OrgID, id)
	if errors.Is(err, ErrNotFound) {
		writeError(w, http.StatusNotFound, "note not found")
		return
	}
	if err != nil {
		internalError(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) writeNoteResult(w http.ResponseWriter, status int, n Note, err error) {
	if errors.Is(err, ErrNotFound) {
		writeError(w, http.StatusNotFound, "note not found")
		return
	}
	if err != nil {
		internalError(w, err)
		return
	}
	writeJSON(w, status, n)
}

// --- helpers ---

func decodeJSON(w http.ResponseWriter, r *http.Request, v any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, maxBodyBytes)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		var tooLarge *http.MaxBytesError
		if errors.As(err, &tooLarge) {
			writeError(w, http.StatusRequestEntityTooLarge, "request body too large")
		} else {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
		}
		return false
	}
	if err := dec.Decode(&struct{}{}); err != io.EOF {
		writeError(w, http.StatusBadRequest, "request body must contain a single JSON object")
		return false
	}
	return true
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("write response: %v", err)
	}
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func internalError(w http.ResponseWriter, err error) {
	log.Printf("internal error: %v", err)
	writeError(w, http.StatusInternalServerError, "internal error")
}

func bearerToken(r *http.Request) (string, bool) {
	scheme, token, ok := strings.Cut(r.Header.Get("Authorization"), " ")
	token = strings.TrimSpace(token)
	if !ok || !strings.EqualFold(scheme, "Bearer") || token == "" {
		return "", false
	}
	return token, true
}

func newToken() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

// hashToken is what gets stored, so a leaked database doesn't leak usable tokens.
func hashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

func normaliseEmail(raw string) (string, bool) {
	email := strings.ToLower(strings.TrimSpace(raw))
	addr, err := mail.ParseAddress(email)
	if err != nil || addr.Address != email || len(email) > 254 {
		return "", false
	}
	return email, true
}

func checkPassword(pw string) string {
	switch {
	case len(pw) < minPasswordLen:
		return "password must be at least 8 characters"
	case len(pw) > maxPasswordLen:
		return "password must be at most 72 bytes"
	}
	return ""
}

func pathID(r *http.Request) (int64, bool) {
	id, err := strconv.ParseInt(r.PathValue("id"), 10, 64)
	return id, err == nil && id > 0
}

func intParam(raw string, def, min, max int) (int, bool) {
	if raw == "" {
		return def, true
	}
	n, err := strconv.Atoi(raw)
	if err != nil || n < min || n > max {
		return 0, false
	}
	return n, true
}
