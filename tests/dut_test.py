import asyncio 
import os 
import sys 


from pathlib import Path
import cocotb
from cocotb import runner
from cocotb.runner import get_runner
from cocotb.triggers import Timer, ClockCycles, RisingEdge, Event , ReadOnly
from cocotb.clock import Clock 
from cocotb.log import logging, SimLog
from cocotb_bus.drivers import BusDriver
from cocotb_bus.monitors import BusMonitor
from cocotb_bus.scoreboard import Scoreboard
from cocotb_coverage.coverage import CoverPoint, CoverCross, coverage_db

import random as rnd
import constraint

@CoverPoint("top.a",
            xf = lambda x,y:x,
            bins=[0,1])
@CoverPoint("top.b",
            xf = lambda x,y:y,
            bins=[0,1])
@CoverCross("top.cross.ab",
            items=['top.a','top.b'])
def sample_fnc(x,y):
    pass 

@CoverPoint("top.w.wd_addr",
            xf = lambda wd_addr,wd_en, wd_data, rd_en, rd_addr: wd_addr,
            bins=[4,5])
@CoverPoint("top.w.wd_data",
            xf = lambda wd_addr,wd_en, wd_data, rd_en, rd_addr: wd_data,
            bins=[0,1])
@CoverPoint("top.w.wd_en",
            xf = lambda wd_addr,wd_en, wd_data, rd_en, rd_addr: wd_en,
            bins=[0,1])
@CoverPoint("top.r.rd_addr",
            xf = lambda wd_addr,wd_en, wd_data, rd_en, rd_addr: rd_addr,
            bins=[0,1,2,3])
@CoverPoint("top.r.rd_en",
            xf = lambda wd_addr,wd_en, wd_data, rd_en, rd_addr: rd_en,
            bins=[0,1])
@CoverCross("top.cross.w",
            items=['top.w.wd_addr', 'top.w.wd_data', 'top.w.wd_en'] 
            )
@CoverCross("top.cross.r",
            items=["top.r.rd_en", "top.r.rd_addr"])
def fl_cv(wd_addr, wd_en, wd_data, rd_en, rd_addr):
    pass





class write_Driver(BusDriver):
    _signals=["CLK", "RST_N", "write_address", "write_data", "write_en", "write_rdy", "read_address", "read_en", "read_rdy", "read_data"]
    def __init__(self, name, entity):
        self.name = name
        self.entity= entity
        self.CLK = entity.CLK

    async def _driver_send(self, transaction, sync = True):
        await RisingEdge(self.CLK)
        if (self.entity.write_rdy.value.integer != 1):
            await RisingEdge(self.entity.write_rdy)
        self.entity.write_en.value = 1
        self.entity.write_address.value = transaction.get('addr')
        self.entity.write_data.value = transaction.get('val')
        await RisingEdge(self.CLK)
        self.entity.write_en.value = 0

class read_Driver(BusDriver):

    _signals=["CLK", "RST_N", "write_address", "write_data", "write_en", "write_rdy", "read_address", "read_en", "readd_rdy", "read_data"]
    def __init__(self, name, entity):
        self.name = name
        self.entity= entity
        self.CLK = entity.CLK

    async def _driver_send(self, transaction, sync= True):
        await RisingEdge(self.CLK)
        if (self.entity.read_rdy.value.integer != 1):
            await RisingEdge(self.entity.read_rdy)
        self.entity.read_en.value = 1
        self.entity.read_address.value = transaction.get('addr')
        await RisingEdge(self.CLK)
        self.entity.read_en.value = 0

class TB:
    def __init__(self, name, entity, log):
        self.log = log 
        self.name = name 
        self.entity = entity
        self.CLK=self.entity.CLK 
        self.a_ls = []
        self.b_ls = []
        self.y_ls = []
        self.stats=[]
        self.writer_event= Event()
        self.reader_event = Event()
        self.ref_address={'A_status':0,'B_status':1,'Y_status':2, 'Y_output':3 , 'A_data':4, 'B_data':5}
        self.writer = write_Driver("Write fifo", entity)
        self.reader = read_Driver("Read fifo", entity)
    
    async def reset_dut(self):
        await RisingEdge(self.CLK)
        self.entity.write_address.value=0
        self.entity.write_data.value=0
        self.entity.write_en.value=0
        self.entity.read_en.value=0
        self.entity.read_data.value=0
        self.entity.read_address.value=0
        # negative reset 
        self.entity.RST_N.value=1
        await ClockCycles(self.CLK, 4)
        self.entity.RST_N.value=0
        await ClockCycles(self.CLK, 4)
        self.entity.RST_N.value=1
        await RisingEdge(self.CLK)
        print("\t\t reset done")

    def stat_dec(self,addr, val):
        if addr ==  3:
            self.stats.append({'name':'yr','val':val})
        elif addr == 4:
            self.stats.append({'name':'aw', 'val':val})
        elif addr == 5:
            self.stats.append({'name':'bw', 'val':val})
        elif addr == 0:
            self.stats.append({'name':'as', 'val':(f"{'full' if val == 0 else 'empty'}")})
        elif addr == 1:
            self.stats.append({'name':'bs', 'val':(f"{'full' if val == 0 else 'empty'}")})
        elif addr == 2:
            self.stats.append({'name':'ys', 'val':(f"{'full' if val == 1 else 'empty'}")})


    def cvr(self):
        self.p = constraint.Problem()
        self.p.addVariable('write_en',[0,1])
        self.p.addVariable('read_en', [0,1])
        self.p.addVariable('write_address', [4,5]) # max 5 
        self.p.addVariable('read_address', [0,1,2,3]) # max 5
        self.p.addVariable('write_data', [0,1])
        self.p.addVariable('write_rdy', [1])
        self.p.addVariable('read_rdy', [1])
    
        self.p.addConstraint(lambda rd_en, wd_en, rd_rdy: rd_en == 1 if wd_en == 0 and rd_rdy == 1 else rd_en == 0, ['read_en', 'write_en', 'read_rdy']) # rd when not write is present and rd_rdy is asserted
        self.p.addConstraint(lambda rd_en, wd_en, wd_rdy: wd_en == 1 if rd_en == 0 and wd_rdy == 1 else wd_en == 0, ['read_en', 'write_en', 'write_rdy']) # rd when not write is present and rd_rdy is asserted
        #self.p.addConstraint(lambda wd_addr,rd_en, wd_en, wd_dat: wd_addr == 0 and wd_dat == 0 if rd_en == 1 and wd_en == 0 else True, ['write_address', 'read_en', 'write_en', 'write_data'])
        #self.p.addConstraint(lambda rd_addr,rd_en, wd_en: rd_addr == 0 if rd_en == 0 and wd_en == 1 else True, ['read_address', 'read_en', 'write_en'])

    def solve(self):
        self.cvr_obj = self.cvr()
        self.sols = self.p.getSolutions()

    def get_sols(self):
        return rnd.choice(self.sols) if self.sols else None



@cocotb.test()
async def dut_test(dut):
    cocotb.start_soon(Clock(dut.CLK, 2, "ns").start())
    log = SimLog("interface_test")
    logging.getLogger().setLevel(logging.INFO)

    tbh = TB(name="tb inst", entity=dut, log=log)
    
    await tbh.reset_dut()

    # do functional
    await tbh.writer._driver_send(transaction={'addr':4,'val':0})
    await tbh.writer._driver_send(transaction={'addr':5,'val':0})
    sample_fnc(0,0)
    await tbh.reader._driver_send({'addr':3,'val':0})
    log.debug(f"[functional] a:0 b:0 y:{dut.read_data.value.integer}")
    await tbh.writer._driver_send(transaction={'addr':4,'val':0})
    await tbh.writer._driver_send(transaction={'addr':5,'val':1}) 
    sample_fnc(0,1)
    await tbh.reader._driver_send({'addr':3,'val':0})
    log.debug(f"[functional] a:0 b:1 y:{dut.read_data.value.integer}")
    await tbh.writer._driver_send(transaction={'addr':4,'val':1})
    await tbh.writer._driver_send(transaction={'addr':5,'val':0}) 
    sample_fnc(1,0)
    await tbh.reader._driver_send({'addr':3,'val':0})
    log.debug(f"[functional] a:1 b:0 y:{dut.read_data.value.integer}")
    await tbh.writer._driver_send(transaction={'addr':4,'val':1})
    await tbh.writer._driver_send(transaction={'addr':5,'val':1}) 
    sample_fnc(1,1)
    await tbh.reader._driver_send({'addr':3,'val':0})
    log.debug(f"[functional] a:1 b:1 y:{dut.read_data.value.integer}")

    tbh.solve()
    for i in range(32):
        x = tbh.get_sols()
        fl_cv(x.get("write_address"), x.get("write_data"), x.get("write_en"), x.get("read_en"), x.get("read_address"))
        if x.get('read_en') == 1:
            await tbh.reader._driver_send(transaction={'addr':x.get('read_address'), 'val':0 })
            log.debug(f"[{i}][read  operation] address: {x.get('read_address')} got data: {dut.read_data.value.integer}")
            tbh.stat_dec(x.get('read_address'), dut.read_data.value.integer)
        elif x.get('write_en') == 1:
            await tbh.writer._driver_send(transaction={'addr':x.get('write_address'), 'val': x.get('write_data') })
            log.debug(f"[{i}][write operation] address: {x.get('write_address')} put data: {x.get('write_data')}")
            tbh.stat_dec(x.get('write_address'), x.get('write_data'))
        await RisingEdge(dut.CLK)

    for i in tbh.stats:
        log.debug(f"{i}")

    coverage_db.report_coverage(log.info,bins=True)
    log.info(f"Functional Coverage: {coverage_db['top.cross.ab'].cover_percentage:.2f} %")
    log.info(f"Write Coverage: {coverage_db['top.cross.w'].cover_percentage:.2f} %")
    log.info(f"Read Coverage: {coverage_db['top.cross.r'].cover_percentage:.2f} %")
     


def start_build():
    sim = os.getenv("SIM","verilator")
    dut_dir = Path(__file__).resolve().parent.parent
    dut_dir = f"{dut_dir}/hdl"
    hdl_toplevel="dut"
    verilog_sources = [f"{dut_dir}/{hdl_toplevel}.v", f"{dut_dir}/FIFO1.v",f"{dut_dir}/FIFO2.v"]
    build_args = ["--trace", "--trace-fst"]
    
    runner = get_runner(sim)

    runner.build(
        hdl_toplevel=hdl_toplevel,
        verilog_sources=verilog_sources,
        build_args=build_args,
        waves=True,
        always=True
    )

    runner.test(
    test_module="dut_test",
        hdl_toplevel=hdl_toplevel,
        waves=True
    )

if __name__ == "__main__":
    start_build()
