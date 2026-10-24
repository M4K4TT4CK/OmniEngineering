package planted.cwe25;

import java.io.DataInputStream;
import java.io.IOException;

/** CWE-190: sizes and ports parsed from text wrap around instead of failing. */
public class Cwe190IntegerOverflow {
    private static final int RECORD_SIZE = 64;

    /** count=40000000 makes count * 64 wrap negative (or small), and the read overruns the plan. */
    public byte[] readRecords(String countHeader, DataInputStream in) throws IOException {
        int count = Integer.parseInt(countHeader);
        byte[] buf = new byte[count * RECORD_SIZE]; // expect: arbiter/java.parsed-count-overflow
        in.readFully(buf);
        return buf;
    }

    /** A hop limit of "300" becomes 44: the cast keeps the low 8 bits. */
    public byte hopLimit(String value) {
        return (byte) Integer.parseInt(value); // expect: arbiter/java.parsed-number-narrowed
    }

    /** "3000000000" ms becomes a negative timeout. */
    public int timeoutMillis(String value) {
        return (int) Long.parseLong(value); // expect: arbiter/java.parsed-number-narrowed
    }
}
