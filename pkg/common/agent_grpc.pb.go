// Code generated by protoc-gen-go-grpc. DO NOT EDIT.
// versions:
// - protoc-gen-go-grpc v1.5.1
// - protoc             v4.25.2
// source: pkg/common/proto/agent.proto

package common

import (
	context "context"
	grpc "google.golang.org/grpc"
	codes "google.golang.org/grpc/codes"
	status "google.golang.org/grpc/status"
)

// This is a compile-time assertion to ensure that this generated file
// is compatible with the grpc package it is being compiled against.
// Requires gRPC-Go v1.64.0 or later.
const _ = grpc.SupportPackageIsVersion9

const (
	AgentService_DraftPlayer_FullMethodName          = "/AgentService/DraftPlayer"
	AgentService_SubmitFantasyActions_FullMethodName = "/AgentService/SubmitFantasyActions"
)

// AgentServiceClient is the client API for AgentService service.
//
// For semantics around ctx use and closing/ending streaming RPCs, please refer to https://pkg.go.dev/google.golang.org/grpc/?tab=doc#ClientConn.NewStream.
type AgentServiceClient interface {
	DraftPlayer(ctx context.Context, in *GameState, opts ...grpc.CallOption) (*DraftSelection, error)
	SubmitFantasyActions(ctx context.Context, in *GameState, opts ...grpc.CallOption) (*AttemptedFantasyActions, error)
}

type agentServiceClient struct {
	cc grpc.ClientConnInterface
}

func NewAgentServiceClient(cc grpc.ClientConnInterface) AgentServiceClient {
	return &agentServiceClient{cc}
}

func (c *agentServiceClient) DraftPlayer(ctx context.Context, in *GameState, opts ...grpc.CallOption) (*DraftSelection, error) {
	cOpts := append([]grpc.CallOption{grpc.StaticMethod()}, opts...)
	out := new(DraftSelection)
	err := c.cc.Invoke(ctx, AgentService_DraftPlayer_FullMethodName, in, out, cOpts...)
	if err != nil {
		return nil, err
	}
	return out, nil
}

func (c *agentServiceClient) SubmitFantasyActions(ctx context.Context, in *GameState, opts ...grpc.CallOption) (*AttemptedFantasyActions, error) {
	cOpts := append([]grpc.CallOption{grpc.StaticMethod()}, opts...)
	out := new(AttemptedFantasyActions)
	err := c.cc.Invoke(ctx, AgentService_SubmitFantasyActions_FullMethodName, in, out, cOpts...)
	if err != nil {
		return nil, err
	}
	return out, nil
}

// AgentServiceServer is the server API for AgentService service.
// All implementations must embed UnimplementedAgentServiceServer
// for forward compatibility.
type AgentServiceServer interface {
	DraftPlayer(context.Context, *GameState) (*DraftSelection, error)
	SubmitFantasyActions(context.Context, *GameState) (*AttemptedFantasyActions, error)
	mustEmbedUnimplementedAgentServiceServer()
}

// UnimplementedAgentServiceServer must be embedded to have
// forward compatible implementations.
//
// NOTE: this should be embedded by value instead of pointer to avoid a nil
// pointer dereference when methods are called.
type UnimplementedAgentServiceServer struct{}

func (UnimplementedAgentServiceServer) DraftPlayer(context.Context, *GameState) (*DraftSelection, error) {
	return nil, status.Errorf(codes.Unimplemented, "method DraftPlayer not implemented")
}
func (UnimplementedAgentServiceServer) SubmitFantasyActions(context.Context, *GameState) (*AttemptedFantasyActions, error) {
	return nil, status.Errorf(codes.Unimplemented, "method SubmitFantasyActions not implemented")
}
func (UnimplementedAgentServiceServer) mustEmbedUnimplementedAgentServiceServer() {}
func (UnimplementedAgentServiceServer) testEmbeddedByValue()                      {}

// UnsafeAgentServiceServer may be embedded to opt out of forward compatibility for this service.
// Use of this interface is not recommended, as added methods to AgentServiceServer will
// result in compilation errors.
type UnsafeAgentServiceServer interface {
	mustEmbedUnimplementedAgentServiceServer()
}

func RegisterAgentServiceServer(s grpc.ServiceRegistrar, srv AgentServiceServer) {
	// If the following call pancis, it indicates UnimplementedAgentServiceServer was
	// embedded by pointer and is nil.  This will cause panics if an
	// unimplemented method is ever invoked, so we test this at initialization
	// time to prevent it from happening at runtime later due to I/O.
	if t, ok := srv.(interface{ testEmbeddedByValue() }); ok {
		t.testEmbeddedByValue()
	}
	s.RegisterService(&AgentService_ServiceDesc, srv)
}

func _AgentService_DraftPlayer_Handler(srv interface{}, ctx context.Context, dec func(interface{}) error, interceptor grpc.UnaryServerInterceptor) (interface{}, error) {
	in := new(GameState)
	if err := dec(in); err != nil {
		return nil, err
	}
	if interceptor == nil {
		return srv.(AgentServiceServer).DraftPlayer(ctx, in)
	}
	info := &grpc.UnaryServerInfo{
		Server:     srv,
		FullMethod: AgentService_DraftPlayer_FullMethodName,
	}
	handler := func(ctx context.Context, req interface{}) (interface{}, error) {
		return srv.(AgentServiceServer).DraftPlayer(ctx, req.(*GameState))
	}
	return interceptor(ctx, in, info, handler)
}

func _AgentService_SubmitFantasyActions_Handler(srv interface{}, ctx context.Context, dec func(interface{}) error, interceptor grpc.UnaryServerInterceptor) (interface{}, error) {
	in := new(GameState)
	if err := dec(in); err != nil {
		return nil, err
	}
	if interceptor == nil {
		return srv.(AgentServiceServer).SubmitFantasyActions(ctx, in)
	}
	info := &grpc.UnaryServerInfo{
		Server:     srv,
		FullMethod: AgentService_SubmitFantasyActions_FullMethodName,
	}
	handler := func(ctx context.Context, req interface{}) (interface{}, error) {
		return srv.(AgentServiceServer).SubmitFantasyActions(ctx, req.(*GameState))
	}
	return interceptor(ctx, in, info, handler)
}

// AgentService_ServiceDesc is the grpc.ServiceDesc for AgentService service.
// It's only intended for direct use with grpc.RegisterService,
// and not to be introspected or modified (even as a copy)
var AgentService_ServiceDesc = grpc.ServiceDesc{
	ServiceName: "AgentService",
	HandlerType: (*AgentServiceServer)(nil),
	Methods: []grpc.MethodDesc{
		{
			MethodName: "DraftPlayer",
			Handler:    _AgentService_DraftPlayer_Handler,
		},
		{
			MethodName: "SubmitFantasyActions",
			Handler:    _AgentService_SubmitFantasyActions_Handler,
		},
	},
	Streams:  []grpc.StreamDesc{},
	Metadata: "pkg/common/proto/agent.proto",
}
