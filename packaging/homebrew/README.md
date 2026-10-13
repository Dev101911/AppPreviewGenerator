# Homebrew formula (draft)

Until a tap exists, Homebrew users can install with:

```bash
brew install pipx && pipx install apppreview
```

To publish a real formula after the first PyPI release:

1. Create a tap repository named `homebrew-tap` under the same GitHub owner
   (`brew tap-new Dev101911/tap` creates one locally).
2. Copy `apppreview.rb` into the tap's `Formula/` folder.
3. Set `url` to the release's sdist on PyPI and `sha256` to its checksum
   (`curl -L <url> | shasum -a 256`).
4. Run `brew update-python-resources Formula/apppreview.rb` to generate the
   dependency `resource` blocks.
5. Run `brew install --build-from-source Formula/apppreview.rb`, then
   `brew test apppreview` and `brew audit --strict --new apppreview`.
6. Push the tap. Users then run `brew install Dev101911/tap/apppreview`.

For later releases, update `url` and `sha256`, re-run step 4, and push.
