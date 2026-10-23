# Secrets

## The check

`llmpro check` has two parts.

1. Patterns. It looks for known secret formats, such as cloud keys, tokens and private key blocks.
2. Leaks. It collects the secret values that appear in the assistants' own raw logs, then searches every file in the store for them. Short pieces of a value count too. Part 2 finds a secret that has no known format.

Options: `--staged` checks only what is staged. `--history` checks every file and every blob in history, which a push needs. `--part 1` or `--part 2` runs one part. `--json` gives the result as JSON.

The pre-commit and pre-merge-commit hooks in the store run it. A commit that fails the check is refused.

## When it reports a hit

1. It shows a masked value and a fingerprint, never the value. Do not try to print the value.
2. Find the file and line it names and remove the secret. If the secret came from a chat, redact the line or delete the file, then run `llmpro sync --local` again.
3. If the secret was real and was ever committed or pushed, tell the user. It must be rotated, because removing it from the files does not remove it from history.

## False positives

A false positive goes into `.agentspro/allowlist.json` in the store, by fingerprint, with a reason. Only add one when the user agrees that the value is not a secret.

## Secrets no rule finds

A secret with no shape a rule can find (a random value with nothing around it that says it is a secret) goes into `.agentspro/blocklist.json` in the store, the same way: `{"<fingerprint>": "reason"}`. The redactor then masks it as `[REDACTED:listed]` and the check and the push guard report it. The file is committed and reaches every machine, and a fingerprint of a short or common password can be found again by trying candidates, so list only long random values. Compute the fingerprint without printing the value, and let the user add the line.

## The push guard

The push guard checks what a push adds to any repo, not only the store. It is a `pre-push` script that gitpro's global hook runs through `[guard] chain_to` in the user's `~/.config/gitpro.toml`. `llmpro setup` writes the script, adds the `chain_to` line to that file (keeping a copy), and runs `gitpro setup -y --agent none`. If the file is one it cannot edit safely, it prints the line for the user to add. Tell the user to run `llmpro setup`, not `gitpro setup` on its own.

- A push with a secret in it is stopped. The message names the commit, the file and the line.
- To let one push go through when the user says so: `LLMPRO_SKIP=1 git push`. Do not set it on your own.
- The guard fails open: if it has trouble of its own, the push goes on.

## Never

- Never print a secret, a long token or a raw session file.
- Never copy a secret into a memory, a note or a commit message.
- Never add a git remote to the store or push it.
