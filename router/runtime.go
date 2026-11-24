package main

import (
	"os"
	"path/filepath"
	"strconv"
)

func (r *Router) runtimeFile(name string) string {
	if r.runtimeDir == "" {
		return filepath.Join(r.dir, name)
	}
	return filepath.Join(r.runtimeDir, name)
}

func (r *Router) initRuntime() error {
	if r.runtimeDir == "" {
		return nil
	}
	if e := os.MkdirAll(r.runtimeDir, 0700); e != nil {
		return e
	}
	for _, name := range []string{"status.json", "project-pool.json"} {
		target := r.runtimeFile(name)
		if _, e := os.Stat(target); os.IsNotExist(e) {
			if e = atomicWrite(target, []byte("{}")); e != nil {
				return e
			}
		} else if e != nil {
			return e
		}
		link := filepath.Join(r.dir, name)
		if existing, e := os.Readlink(link); e == nil && existing == target {
			continue
		}
		temporary := link + ".runtime-" + strconv.Itoa(os.Getpid())
		if e := os.Symlink(target, temporary); e != nil {
			return e
		}
		e := os.Rename(temporary, link)
		os.Remove(temporary)
		if e != nil {
			return e
		}
	}
	return nil
}
