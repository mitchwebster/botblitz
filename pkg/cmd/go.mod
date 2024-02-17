module github.com/mitchwebster/botblitz/pkg/runner

go 1.21

replace github.com/mitchwebster/botblitz/pkg/common v0.0.0 => ../common

replace github.com/mitchwebster/botblitz/pkg/engine v0.0.0 => ../engine

require (
	github.com/mitchwebster/botblitz/pkg/common v0.0.0
	github.com/mitchwebster/botblitz/pkg/engine v0.0.0
)

require (
	github.com/golang/protobuf v1.5.3 // indirect
	golang.org/x/net v0.18.0 // indirect
	golang.org/x/sys v0.14.0 // indirect
	golang.org/x/text v0.14.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20231106174013-bbf56f31fb17 // indirect
)

require (
	google.golang.org/grpc v1.61.1 // indirect
	google.golang.org/protobuf v1.32.0 // indirect
)
