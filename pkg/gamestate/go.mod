module github.com/mitchwebster/botblitz/pkg/gamestate

go 1.24.2

require (
	github.com/mitchwebster/botblitz/pkg/common v0.0.0
	gorm.io/driver/sqlite v1.5.5
	gorm.io/gorm v1.25.7
)

require (
	github.com/jinzhu/inflection v1.0.0 // indirect
	github.com/jinzhu/now v1.1.5 // indirect
	github.com/mattn/go-sqlite3 v1.14.17 // indirect
	golang.org/x/net v0.29.0 // indirect
	golang.org/x/sys v0.25.0 // indirect
	golang.org/x/text v0.18.0 // indirect
	google.golang.org/genproto/googleapis/rpc v0.0.0-20240903143218-8af14fe29dc1 // indirect
	google.golang.org/grpc v1.66.2 // indirect
	google.golang.org/protobuf v1.34.2 // indirect
)

replace github.com/mitchwebster/botblitz/pkg/common v0.0.0 => ../common
