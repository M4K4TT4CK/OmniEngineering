package cwe25

import (
	"database/sql"
	"log/slog"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
)

// ListUsers logs the driver's error and returns a fixed message with the 500.
func ListUsers(db *sql.DB) gin.HandlerFunc {
	return func(c *gin.Context) {
		limit, err := strconv.Atoi(c.DefaultQuery("limit", "50"))
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}
		rows, err := db.QueryContext(c.Request.Context(), "SELECT id, name FROM users LIMIT $1", limit)
		if err != nil {
			slog.Error("list users", "err", err)
			c.JSON(http.StatusInternalServerError, gin.H{"error": "internal error"})
			return
		}
		defer rows.Close()
		c.JSON(http.StatusOK, gin.H{"ok": true})
	}
}
