package com.example.customerimport;

import java.util.ArrayList;
import java.util.List;

/**
 * Minimal RFC 4180 CSV parser.
 *
 * <p>Malformed records do not abort parsing: they are returned with {@link Row#error()} set so
 * the caller can report them as per-row errors and carry on with the rest of the file.
 */
public final class Csv {

    /** One CSV record. {@code line} is the 1-based physical line on which the record starts. */
    public record Row(int line, List<String> fields, String error) {}

    private Csv() {}

    /** Parses {@code text}, accepting LF, CRLF and CR line endings. Blank lines are skipped. */
    public static List<Row> parse(String text) {
        List<Row> rows = new ArrayList<>();
        List<String> fields = new ArrayList<>();
        StringBuilder field = new StringBuilder();
        String error = null;
        boolean quoted = false;   // the current field started with a quote
        boolean inQuotes = false; // we are between a field's opening and closing quotes
        int line = 1;
        int rowLine = 1;
        int i = 0;
        int n = text.length();

        while (i < n) {
            char c = text.charAt(i++);
            if (inQuotes) {
                if (c == '"') {
                    if (i < n && text.charAt(i) == '"') {
                        field.append('"');
                        i++;
                    } else {
                        inQuotes = false;
                    }
                } else {
                    if (c == '\n' || (c == '\r' && !(i < n && text.charAt(i) == '\n'))) {
                        line++;
                    }
                    field.append(c);
                }
            } else if (c == ',') {
                fields.add(field.toString());
                field.setLength(0);
                quoted = false;
            } else if (c == '\r' || c == '\n') {
                if (c == '\r' && i < n && text.charAt(i) == '\n') {
                    i++;
                }
                fields.add(field.toString());
                addRow(rows, rowLine, fields, quoted, error);
                fields = new ArrayList<>();
                field.setLength(0);
                quoted = false;
                error = null;
                line++;
                rowLine = line;
            } else if (c == '"' && field.length() == 0 && !quoted) {
                quoted = true;
                inQuotes = true;
            } else {
                if (quoted && error == null) {
                    error = "unexpected character after closing quote";
                }
                // A quote inside an unquoted field is kept literally, as spreadsheets do.
                field.append(c);
            }
        }

        if (inQuotes) {
            error = "unterminated quoted field";
        }
        if (!fields.isEmpty() || field.length() > 0 || quoted) {
            fields.add(field.toString());
            addRow(rows, rowLine, fields, quoted, error);
        }
        return rows;
    }

    private static void addRow(List<Row> rows, int line, List<String> fields, boolean lastQuoted, String error) {
        boolean blank = fields.size() == 1 && fields.get(0).isEmpty() && !lastQuoted;
        if (!blank) {
            rows.add(new Row(line, List.copyOf(fields), error));
        }
    }
}
