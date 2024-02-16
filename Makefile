clean:
	rm -f pkg/common/agent.pb.go
	rm -f pkg/common/agent_grpc.pb.go
	rm -f pybot/agent_pb2.py
	rm -f pybot/agent_pb2.pyi
	rm -f pybot/agent_pb2_grpc.py

gen:
	protoc ./pkg/common/proto/agent.proto --go_out=./pkg/common/ --go-grpc_out=./pkg/common/
	cd pybot && python -m grpc_tools.protoc -I ../pkg/common/proto --python_out=. --pyi_out=. --grpc_python_out=. ../pkg/common/proto/agent.proto

test:
	go list -f '{{.Dir}}' -m | xargs -L1 go mod tidy -C
	go list -f '{{.Dir}}' -m | xargs -L1 go work sync -C
	go list -f '{{.Dir}}' -m | xargs -L1 go test -C

run-engine:
	go run pkg/cmd/engine_bootstrap.go