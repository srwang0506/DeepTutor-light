// Command paper-compiler runs a private manuscript snapshot in a WASM guest.
package main

import (
	"context"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"

	"github.com/mgilbir/tecgonic"
)

func run() error {
	if len(os.Args) != 3 || !fs.ValidPath(os.Args[2]) || !strings.EqualFold(path.Ext(os.Args[2]), ".tex") {
		return fmt.Errorf("expected a bundle directory and relative main TeX file")
	}
	bundle, main := os.Args[1], os.Args[2]
	source, err := os.OpenRoot(".")
	if err != nil {
		return err
	}
	defer source.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 85*time.Second)
	defer cancel()
	cache := os.Getenv("DEEPTUTOR_TECTONIC_CACHE")
	if cache == "" {
		cache = filepath.Join(os.TempDir(), "deeptutor-tectonic-cache")
	}
	// The package supplies independent, read-only guest mounts for the bundle
	// and manuscript. The guest receives no host network, environment or home.
	compiler, err := tecgonic.New(ctx, tecgonic.WithDefaultBundleDir(bundle),
		tecgonic.WithDefaultFontsDir(bundle), tecgonic.WithContextCancellation(),
		tecgonic.WithCompilationCache(cache),
		tecgonic.WithMemoryLimitMiB(384))
	if err != nil {
		return err
	}
	defer compiler.Close(context.Background())
	pdf, err := compiler.Compile(ctx, source.FS(), main, tecgonic.WithStderr(os.Stderr),
		tecgonic.WithBuildDate(time.Unix(1700000000, 0)))
	if err != nil {
		return err
	}
	name := strings.TrimSuffix(path.Base(main), path.Ext(main)) + ".pdf"
	return source.WriteFile(name, pdf, 0600)
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
