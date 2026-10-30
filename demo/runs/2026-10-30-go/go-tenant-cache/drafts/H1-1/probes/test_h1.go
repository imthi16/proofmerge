package pricing

import (
	"strings"
	"testing"
)

func TestProofMergeProbe(t *testing.T) {
	p1, err1 := PriceFor("tenant-a", "widget")
	if err1 != nil {
		t.Fatalf("first call unexpected error: %v", err1)
	}
	if p1 != 25.0 {
		t.Fatalf("first call price = %v, want 25.0", p1)
	}
	p2, err2 := PriceFor("no-such-tenant", "widget")
	if err2 == nil {
		t.Fatalf("second call with unknown tenant succeeded with price %v, want unknown-tenant error", p2)
	}
	if !strings.Contains(err2.Error(), "unknown tenant: no-such-tenant") {
		t.Fatalf("second call error = %q, want unknown tenant: no-such-tenant", err2.Error())
	}
	if p2 != 0 {
		t.Fatalf("second call price = %v, want 0 on error", p2)
	}
}
