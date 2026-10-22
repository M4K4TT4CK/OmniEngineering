package cwe25

import (
	"database/sql"
	"net/http"

	"github.com/gin-gonic/gin"
)

// ListUsers is CWE-200: a gin handler that returns the driver's error text with a 500.
func ListUsers(db *sql.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		rows, err := db.QueryContext(c.Request.Context(), "SELECT id, name FROM users")
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()}) // expect: arbiter/go.error-text-in-response
			return
		}
		defer rows.Close()
		c.JSON(http.StatusOK, gin.H{"ok": true})
	}
}
