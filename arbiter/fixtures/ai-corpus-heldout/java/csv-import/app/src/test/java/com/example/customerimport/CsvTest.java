package com.example.customerimport;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

import java.util.List;
import org.junit.jupiter.api.Test;

class CsvTest {

    @Test
    void parsesQuotedFieldsAndTracksStartingLines() {
        List<Csv.Row> rows = Csv.parse("a,b\n\"x\ny\",\"say \"\"hi\"\"\"\nq,\n");

        assertEquals(3, rows.size());
        assertEquals(new Csv.Row(1, List.of("a", "b"), null), rows.get(0));
        assertEquals(new Csv.Row(2, List.of("x\ny", "say \"hi\""), null), rows.get(1));
        assertEquals(new Csv.Row(4, List.of("q", ""), null), rows.get(2));
    }

    @Test
    void skipsBlankLinesButKeepsQuotedEmptyRow() {
        List<Csv.Row> rows = Csv.parse("a\r\n\r\n\"\"\nb");

        assertEquals(List.of(List.of("a"), List.of(""), List.of("b")),
                rows.stream().map(Csv.Row::fields).toList());
        assertEquals(4, rows.get(2).line());
    }

    @Test
    void keepsLiteralQuoteInsideUnquotedField() {
        Csv.Row row = Csv.parse("5\" disk,x").get(0);

        assertEquals(List.of("5\" disk", "x"), row.fields());
        assertNull(row.error());
    }

    @Test
    void flagsTextAfterClosingQuote() {
        List<Csv.Row> rows = Csv.parse("\"a\"b,c\nd,e\n");

        assertNotNull(rows.get(0).error());
        assertNull(rows.get(1).error());
    }

    @Test
    void flagsUnterminatedQuote() {
        List<Csv.Row> rows = Csv.parse("a,b\n\"c,d\n");

        assertEquals(2, rows.size());
        assertEquals("unterminated quoted field", rows.get(1).error());
    }
}
