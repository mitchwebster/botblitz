clean:
	rm pkg/common/pb/*

gen:
	protoc ./pkg/common/proto/agent.proto --go_out=./pkg/common/pb/

test:
	go list -f '{{.Dir}}' -m | xargs -L1 go mod tidy -C
	go list -f '{{.Dir}}' -m | xargs -L1 go work sync -C
	go list -f '{{.Dir}}' -m | xargs -L1 go test -C