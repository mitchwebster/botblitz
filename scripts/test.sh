#!/bin/bash

go list -f '{{.Dir}}' -m | xargs -L1 go mod tidy -C
go list -f '{{.Dir}}' -m | xargs -L1 go work sync -C
go list -f '{{.Dir}}' -m | xargs -L1 go test -C