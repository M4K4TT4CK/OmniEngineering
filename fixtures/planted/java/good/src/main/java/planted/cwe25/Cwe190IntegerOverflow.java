package planted.cwe25;

import java.io.DataInputStream;
import java.io.IOException;

/** CWE-190: the parsed values are range-checked, or converted with an exact cast. */
public class Cwe190IntegerOverflow {
    private static final int RECORD_SIZE = 64;
    private static final int MAX_RECORDS = 10_000;

    public byte[] readRecords(String countHeader, DataInputStream in) throws IOException {
        int count = Integer.parseInt(countHeader);
        if (count < 0 || count > MAX_RECORDS) {
            throw new IllegalArgumentException("count out of range");
        }
        byte[] buf = new byte[count * RECORD_SIZE];
        in.readFully(buf);
        return buf;
    }

    public byte hopLimit(String value) {
        return Byte.parseByte(value);
    }

    public int timeoutMillis(String value) {
        return Math.toIntExact(Long.parseLong(value));
    }
}
