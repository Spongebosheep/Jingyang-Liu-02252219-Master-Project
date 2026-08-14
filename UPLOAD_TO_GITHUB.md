# Upload checklist for the existing GitHub repository

Target repository:
`https://github.com/Spongebosheep/Jingyang-Liu-02252219-Master-Project.git`

The target repository is public. Upload only this curated public directory, not
the original full ZIP.

1. Extract the delivered public ZIP.
2. Clone the existing repository to a new local folder.
3. Copy the contents of the extracted public directory into the cloned folder.
4. Remove obsolete old project files from the clone, but never remove the
   clone's `.git` directory.
5. Review the exact staged change before pushing:

```bash
git add -A
git status
git diff --cached --stat
git diff --cached -- . ':!*.zip' ':!*.xlsx' ':!*.pdf' ':!*.png'
```

6. Commit and push only after confirming that no identity, consent, database,
   virtual environment or credential file appears in `git status`:

```bash
git commit -m "Publish final referral revision and curated evidence"
git push origin main
```

After pushing, open the GitHub repository and verify that the root README,
final report, evidence map and public-release exclusion record render correctly.
Do not use `git push --force` for this update.
