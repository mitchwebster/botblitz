clean:
	rm -f pkg/common/agent.pb.go

gen:
	protoc ./pkg/common/proto/agent.proto --go_out=./pkg/common/

test:
	go list -f '{{.Dir}}' -m | xargs -L1 go mod tidy -C
	go list -f '{{.Dir}}' -m | xargs -L1 go work sync -C
	go list -f '{{.Dir}}' -m | xargs -L1 go test -C

run-engine:
	go run pkg/cmd/engine_bootstrap.go