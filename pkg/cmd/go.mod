module github.com/mitchwebster/botblitz/pkg/runner

go 1.21

replace github.com/mitchwebster/botblitz/pkg/common v0.0.0 => ../common

replace github.com/mitchwebster/botblitz/pkg/engine v0.0.0 => ../engine

require (
	github.com/mitchwebster/botblitz/pkg/common v0.0.0
	github.com/mitchwebster/botblitz/pkg/engine v0.0.0
)

require google.golang.org/protobuf v1.32.0 // indirect
