package pgdump

import "strings"

// NormalizeTablePattern removes quotes around patterns that contain wildcards (* or ?).
// In PostgreSQL, quoted identifiers treat * and ? as literal characters, so public."Ware_*"
// would match only a table literally named "Ware_*", not Ware_Monat_1, Ware_Woche_1, etc.
// By removing quotes when wildcards are present, pg_dump's pattern matching works correctly.
// Case is preserved: pg_dump uses case-sensitive fnmatch-style matching.
func NormalizeTablePattern(pattern string) string {
	if !strings.Contains(pattern, "*") && !strings.Contains(pattern, "?") {
		return pattern
	}

	return strings.ReplaceAll(pattern, `"`, "")
}
