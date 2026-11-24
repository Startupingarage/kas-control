package main

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestRuntimeTelemetryLeavesDurableConfigUntouched(t *testing.T) {
	r := Router{dir: t.TempDir(), runtimeDir: t.TempDir()}
	config := filepath.Join(r.dir, "routes.json")
	if e := os.WriteFile(config, []byte("durable"), 0600); e != nil {
		t.Fatal(e)
	}
	if e := r.initRuntime(); e != nil {
		if runtime.GOOS == "windows" {
			t.Skip("Windows symlink privilege unavailable: ", e)
		}
		t.Fatal(e)
	}
	if e := atomicWrite(r.runtimeFile("status.json"), []byte(`{"ready":true}`)); e != nil {
		t.Fatal(e)
	}
	b, e := os.ReadFile(filepath.Join(r.dir, "status.json"))
	if e != nil || string(b) != `{"ready":true}` {
		t.Fatal("compatibility link broken", e)
	}
	if e = r.initRuntime(); e != nil {
		t.Fatal("reinitialization failed", e)
	}
	b, e = os.ReadFile(config)
	if e != nil || string(b) != "durable" {
		t.Fatal("configuration changed", e)
	}
}
