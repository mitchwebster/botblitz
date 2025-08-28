clean:
	rm -f pkg/common/agent.pb.go
	rm -f pkg/common/agent_grpc.pb.go
	rm -f blitz_env/agent_pb2.py
	rm -f blitz_env/agent_pb2.pyi
	rm -f blitz_env/agent_pb2_grpc.py
	rm -f py_grpc_server/agent_pb2.py
	rm -f py_grpc_server/agent_pb2.pyi
	rm -f py_grpc_server/agent_pb2_grpc.py

gen:
	protoc ./pkg/common/proto/agent.proto --go_out=./pkg/common/ --go-grpc_out=./pkg/common/
	cd blitz_env && python -m grpc_tools.protoc -I ../pkg/common/proto --python_out=. --pyi_out=. --grpc_python_out=. ../pkg/common/proto/agent.proto
	cp -f blitz_env/agent_pb2.py py_grpc_server/agent_pb2.py
	cp -f blitz_env/agent_pb2.pyi py_grpc_server/agent_pb2.pyi
	cp -f blitz_env/agent_pb2_grpc.py py_grpc_server/agent_pb2_grpc.py

test:
	go list -f '{{.Dir}}' -m | xargs -L1 go mod tidy -C
	go list -f '{{.Dir}}' -m | xargs -L1 go work sync -C
	go list -f '{{.Dir}}' -m | xargs -L1 go test -C

run-draft:
	go run pkg/cmd/engine_bootstrap.go -game_mode=Draft -enable_google_sheets=false

run-fantasy:
	go run pkg/cmd/engine_bootstrap.go -game_mode=WeeklyFantasy

# must be run after make gen 
build-py-module:
	 cp -f py_grpc_server/loadPlayers.py blitz_env/loadPlayers.py
	 cp -f player_ranks_2025.csv blitz_env/player_ranks_2025.csv
	 cp -f player_ranks_2024.csv blitz_env/player_ranks_2024.csv
	 cp -f player_ranks_2023.csv blitz_env/player_ranks_2023.csv
	 cp -f player_ranks_2022.csv blitz_env/player_ranks_2022.csv
	 cp -f player_ranks_2021.csv blitz_env/player_ranks_2021.csv
	 rm -rf build/ dist/ *.egg-info
	 python setup.py sdist bdist_wheel
	
build-docker:
	docker build -f py-server-dockerfile -t py_grpc_server .

debug-docker:
	docker run -v $(pwd)/tmp:/botblitz:ro -p 8080:8080 py_grpc_server