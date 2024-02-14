module github.com/mitchwebster/botblitz/pkg/engine

go 1.21

replace github.com/mitchwebster/botblitz/pkg/common v0.0.0 => ../common

require github.com/mitchwebster/botblitz/pkg/common v0.0.0

require google.golang.org/protobuf v1.32.0 // indirect
