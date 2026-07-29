### GitHub Stats

<p align="left">
  <img src="https://komarev.com/ghpvc/?username=tanvirr007&color=blue&style=flat-square" alt="Profile Views" />
</p>

<p align="left"><img src="https://raw.githubusercontent.com/tanvirr007/tanvirr007/main/github-metrics.svg" /></p>

### Currently i am working on
{{ range recentContributions 5 }}
- [{{ .Repo.Name }}]({{ .Repo.URL }}) - {{ .Repo.Description }}
{{- end }}

### My latest projects
{{ range recentRepos 5 }}
- [{{ .Name }}]({{ .URL }}) - {{ .Description }}
{{- end }}

### My recent Pull Requests
{{ range recentPullRequests 5 }}
- [{{ .Title }}]({{ .URL }}) on [{{ .Repo.Name }}]({{ .Repo.URL }})
{{- end }}

### Recent Stars
{{ range recentStars 5 }}
- [{{ .Repo.Name }}]({{ .Repo.URL }}) - {{ .Repo.Description }}
{{- end }}
